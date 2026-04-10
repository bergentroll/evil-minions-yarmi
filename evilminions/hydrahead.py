'''Implements one evil minion'''

from distutils.dir_util import mkpath
import fnmatch
import hashlib
import logging
import os
import socket
import time
from uuid import UUID, uuid5

import tornado.gen
import zmq

import salt.channel.client

from evilminions.utils import replace_recursively, fun_call_id_variants


def _jid_key_from_pub(load):
    '''Salt sometimes puts jid only on nested job payload; normalize to str for dict lookup.'''
    j = load.get('jid')
    if j is None:
        inner = load.get('load')
        if isinstance(inner, dict):
            j = inner.get('jid')
    if j is None:
        return None
    return str(j)


class HydraHead(object):
    '''Replicates the behavior of a minion'''
    def __init__(self, minion_id, io_loop, keysize, opts, grains, ramp_up_delay, slowdown_factor, reactions, reactions_by_jid,
                 mimic_poll_interval=0.05):
        self.minion_id = minion_id
        self.io_loop = io_loop
        self.ramp_up_delay = ramp_up_delay
        self.slowdown_factor = slowdown_factor
        self.reactions = reactions
        self.reactions_by_jid = reactions_by_jid
        self.mimic_poll_interval = mimic_poll_interval
        self.current_time = 0

        self.current_jobs = []

        # Compute replacement dict
        self.replacements = {
            grains['id']: minion_id,
            grains['machine_id']: hashlib.md5(minion_id.encode('utf-8')).hexdigest(),
            grains['uuid']: str(uuid5(UUID('d77ed710-0deb-47d9-b053-f2fa2ef78106'), minion_id))
        }

        # Override ID settings
        self.opts = opts.copy()
        self.opts['id'] = minion_id

        # Override calculated settings
        self.opts['master_uri'] = 'tcp://%s:4506' % self.opts['master']
        self.opts['master_ip'] = socket.gethostbyname(self.opts['master'])

        # Override directory settings
        pki_dir = '/tmp/%s' % minion_id
        mkpath(pki_dir)

        cache_dir = os.path.join(pki_dir, 'cache', 'minion')
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)

        sock_dir = os.path.join(pki_dir, 'sock', 'minion')
        if not os.path.exists(sock_dir):
            os.makedirs(sock_dir)

        self.opts['pki_dir'] = pki_dir
        self.opts['sock_dir'] = sock_dir
        self.opts['cache_dir'] = cache_dir

        # Override performance settings
        self.opts['keysize'] = keysize
        self.opts['acceptance_wait_time'] = 10
        self.opts['acceptance_wait_time_max'] = 0
        self.opts['auth_tries'] = 600
        self.opts['zmq_filtering'] = False
        self.opts['tcp_keepalive'] = True
        self.opts['tcp_keepalive_idle'] = 300
        self.opts['tcp_keepalive_cnt'] = -1
        self.opts['tcp_keepalive_intvl'] = -1
        self.opts['recon_max'] = 10000
        self.opts['recon_default'] = 1000
        self.opts['recon_randomize'] = True
        self.opts['ipv6'] = False
        self.opts['zmq_monitor'] = False
        self.opts['open_mode'] = False
        self.opts['verify_master_pubkey_sign'] = False
        self.opts['always_verify_signature'] = False

    @tornado.gen.coroutine
    def start(self):
        '''Opens ZeroMQ sockets, starts listening to PUB events and kicks off initial REQs'''
        self.log = logging.getLogger(__name__)
        yield tornado.gen.sleep(self.ramp_up_delay)
        self.log.info("HydraHead %s started", self.opts['id'])

        factory_kwargs = {'timeout': 60, 'safe': True, 'io_loop': self.io_loop}
        pub_channel = salt.channel.client.AsyncPubChannel.factory(self.opts, **factory_kwargs)
        self.tok = pub_channel.auth.gen_token(b'salt')
        yield pub_channel.connect()
        self.req_channel = salt.channel.client.AsyncReqChannel.factory(self.opts, **factory_kwargs)
        yield self.emit_start_event()

        pub_channel.on_recv(self.mimic)
        yield self.mimic({'load': {'fun': None, 'arg': None, 'tgt': [self.minion_id],
                                   'tgt_type': 'list', 'load': None, 'jid': None}})

    @tornado.gen.coroutine
    def emit_start_event(self):
        '''Emits a Salt-compatible minion start event for this evil minion'''
        tag = 'salt/minion/{}/start'.format(self.minion_id)
        ts = int(time.time())
        request = {
            'cmd': '_minion_event',
            'id': self.minion_id,
            'pretag': None,
            'data': 'Minion {} started at {}'.format(self.minion_id, time.strftime('%a %b %d %H:%M:%S %Y')),
            'tag': tag,
            'ts': ts,
        }
        yield self.req_channel.send(request, timeout=60)

    @tornado.gen.coroutine
    def mimic(self, load):
        '''Finds appropriate reactions to a PUB message and dispatches them'''
        load = load.get('load')
        if not isinstance(load, dict):
            return
        fun = load.get('fun')
        tgt = load.get('tgt')
        tgt_type = load.get('tgt_type')

        if tgt_type == 'glob':
            is_targeted = bool(tgt) and fnmatch.fnmatch(self.minion_id, tgt)
        elif tgt_type == 'list':
            if isinstance(tgt, str):
                is_targeted = self.minion_id == tgt
            elif isinstance(tgt, (list, tuple)) and len(tgt) > 256:
                try:
                    is_targeted = self.minion_id in frozenset(tgt)
                except TypeError:
                    is_targeted = self.minion_id in tgt
            elif isinstance(tgt, (list, tuple)):
                is_targeted = self.minion_id in tgt
            else:
                is_targeted = False
        else:
            is_targeted = tgt == self.minion_id

        if not is_targeted:
            # ignore call that targets a different minion
            return

        if fun is None:
            return

        # react in ad-hoc ways to some special calls
        if fun == 'test.ping':
            yield self.react_to_ping(load)
        elif fun == 'saltutil.find_job':
            yield self.react_to_find_job(load)
        elif fun == 'saltutil.running':
            yield self.react_to_running(load)
        else:
            # Wait for real-minion capture: same jid (preferred) or any matching call_id variant.
            call_ids = []
            _seen = set()
            for args in (load.get('arg'), load.get('fun_args')):
                if args is None:
                    continue
                for cid in fun_call_id_variants(load['fun'], args):
                    if cid not in _seen:
                        _seen.add(cid)
                        call_ids.append(cid)
            jid_key = _jid_key_from_pub(load)
            reactions = None
            raw_to = load.get('to')
            try:
                wait_timeout = float(raw_to) if raw_to is not None else 10.0
            except (TypeError, ValueError):
                wait_timeout = 10.0
            deadline = time.time() + max(1.0, min(wait_timeout, 30.0))

            while time.time() < deadline:
                if jid_key and jid_key in self.reactions_by_jid:
                    reactions = self.reactions_by_jid[jid_key]
                    break
                for cid in call_ids:
                    reactions = self.get_reactions(cid)
                    if reactions:
                        break
                if reactions:
                    break
                yield tornado.gen.sleep(self.mimic_poll_interval)

            if not reactions:
                self.log.error("No reaction for %s call_ids=%s jid=%s", fun, call_ids, jid_key)
                yield self.react_no_reaction(load)
                return

            self.current_time = reactions[0]['header']['time']
            yield self.react(load, reactions)

    def get_reactions(self, call_id):
        '''Returns reactions for the specified call_id'''
        reaction_sets = self.reactions.get(call_id)
        if not reaction_sets:
            return None

        # if multiple reactions were produced in different points in time, attempt to respect
        # historical order (pick the one which has the lowest timestamp after the last processed)
        future_reaction_sets = [s for s in reaction_sets if s[0]['header']['time'] >= self.current_time]
        if future_reaction_sets:
            # Same call_id can be learned many times (repeated identical calls). Prefer the
            # most recently captured chain, not list order (oldest-first used to replay stale cmd.run).
            return max(future_reaction_sets, key=lambda s: s[0]['header']['time'])

        # if there are reactions but none of them were recorded later than the last processed one, meaning
        # we are seeing an out-of-order request compared to the original ordering, let's be content and return
        # the last known one. Not optimal but hey, Hydras have no crystal balls
        return reaction_sets[-1]

    @tornado.gen.coroutine
    def react(self, load, original_reactions):
        '''Dispatches reactions in response to typical functions'''
        self.current_jobs.append(load)
        try:
            reactions = replace_recursively(self.replacements, original_reactions)

            pub_call_ids = set()
            for args in (load.get('arg'), load.get('fun_args')):
                if args is None:
                    continue
                for cid in fun_call_id_variants(load['fun'], args):
                    pub_call_ids.add(cid)

            for reaction in reactions:
                request = reaction['load']
                if 'tok' in request:
                    request['tok'] = self.tok
                if request['cmd'] == '_return' and request.get('fun') == load.get('fun'):
                    r_args = request.get('fun_args') or request.get('arg') or []
                    ret_ids = set(fun_call_id_variants(request.get('fun'), r_args))
                    if pub_call_ids and not (pub_call_ids & ret_ids):
                        # Drop a stale _return from an older baseline still present in the same chain.
                        continue
                    request['jid'] = load['jid']
                    if 'metadata' in load and isinstance(request.get('metadata'), dict):
                        request['metadata']['suma-action-id'] = load['metadata'].get('suma-action-id')
                header = reaction['header']
                duration = header['duration']
                yield tornado.gen.sleep(duration * self.slowdown_factor)
                method = header['method']
                kwargs = header['kwargs']
                yield getattr(self.req_channel, method)(request, **kwargs)
        finally:
            try:
                self.current_jobs.remove(load)
            except ValueError:
                pass

    @tornado.gen.coroutine
    def react_no_reaction(self, load):
        '''Returns an explicit error if no baseline reaction is available yet'''
        fun = load.get('fun')
        request = {
            'cmd': '_return',
            'fun': fun,
            'fun_args': load.get('arg') or load.get('fun_args') or [],
            'id': self.minion_id,
            'jid': load.get('jid'),
            'retcode': 1,
            'return': "evil-minions: no real-minion baseline response for '{}' yet; run the command once against a real minion and retry".format(fun),
            'success': False,
        }
        yield self.req_channel.send(request, timeout=60)

    @tornado.gen.coroutine
    def react_to_ping(self, load):
        '''Dispatches a reaction to a ping call'''
        request = {
            'cmd': '_return',
            'fun': load['fun'],
            'fun_args': load['arg'],
            'id': self.minion_id,
            'jid': load['jid'],
            'retcode': 0,
            'return': True,
            'success': True,
        }
        yield self.req_channel.send(request, timeout=60)

    @tornado.gen.coroutine
    def react_to_find_job(self, load):
        '''Dispatches a reaction to a find_job call'''
        jobs = [j for j in self.current_jobs if j['jid'] == load['arg'][0]]
        ret = dict(list(jobs[0].items()) + list({'pid': 1234}.items())) if jobs else {}

        request = {
            'cmd': '_return',
            'fun': load['fun'],
            'fun_args': load['arg'],
            'id': self.minion_id,
            'jid': load['jid'],
            'retcode': 0,
            'return': ret,
            'success': True,
        }
        yield self.req_channel.send(request, timeout=60)

    @tornado.gen.coroutine
    def react_to_running(self, load):
        '''Dispatches a reaction to a running call'''
        request = {
            'cmd': '_return',
            'fun': load['fun'],
            'fun_args': load['arg'],
            'id': self.minion_id,
            'jid': load['jid'],
            'retcode': 0,
            'return': self.current_jobs,
            'success': True,
        }
        yield self.req_channel.send(request, timeout=60)

