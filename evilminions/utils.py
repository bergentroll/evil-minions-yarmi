'''Various utility functions'''

def replace_recursively(replacements, dump):
    '''Replaces occurences of replacements.keys with corresponding values in a list/dict structure, recursively'''
    if isinstance(dump, list):
        return [replace_recursively(replacements, e) for e in dump]

    if isinstance(dump, dict):
        return {k: replace_recursively(replacements, v) for k, v in dump.items()}

    if isinstance(dump, str):
        try:
            result = dump
            for original, new in replacements.items():
                result = result.replace(original, new)
            return result
        except UnicodeDecodeError:
            return dump

    if dump in replacements:
        return replacements[dump]

    return dump

def fun_call_id(fun, args):
    '''Returns a hashable object that represents the call of a function, with actual parameters'''
    clean_args = [_zap_runtime_noise(_zap_uyuni_specifics(_zap_kwarg(arg))) for arg in args or []]
    return (fun, _immutable(clean_args))


def fun_call_id_variants(fun, args):
    '''Multiple call_ids: PUB ``arg`` and REQ ``fun_args`` often differ by trailing kwargs dicts.'''
    args = list(args or [])
    seen = set()
    variants = []
    while True:
        cid = fun_call_id(fun, args)
        if cid not in seen:
            seen.add(cid)
            variants.append(cid)
        if not args or not isinstance(args[-1], dict):
            break
        args = args[:-1]
    return variants

def _zap_kwarg(arg):
    '''Takes a list/dict stucture and returns a copy with '__kwarg__' keys removed'''
    if isinstance(arg, dict):
        return {k: v for k, v in arg.items() if k != '__kwarg__'}
    return arg

def _zap_uyuni_specifics(data):
    '''Takes a list/dict stucture and returns a copy with Uyuni specific varying keys recursively removed'''
    if isinstance(data, dict):
        uyuni_repo = data.get('alias', '').startswith("susemanager:")
        if uyuni_repo:
            return {k: v for k, v in data.items() if k != 'token'}
        else:
            return {k: _zap_uyuni_specifics(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_zap_uyuni_specifics(e) for e in data]
    return data

def _zap_runtime_noise(data):
    '''Remove runtime-varying fields that should not affect reaction matching'''
    noisy_keys = {
        '__pub_arg',
        '__pub_fun',
        '__pub_fun_args',
        '__pub_id',
        '__pub_jid',
        '__pub_pid',
        '__pub_ret',
        '__pub_tgt',
        '__pub_tgt_type',
        '__pub_user',
        'jid',
        'pid',
        'metadata',
    }
    if isinstance(data, dict):
        return {k: _zap_runtime_noise(v) for k, v in data.items() if k not in noisy_keys}
    if isinstance(data, list):
        return [_zap_runtime_noise(e) for e in data]
    return data

def _immutable(data):
    '''Returns an immutable version of a list/dict stucture'''
    if isinstance(data, dict):
        return tuple((k, _immutable(v)) for k, v in sorted(data.items()))
    if isinstance(data, list):
        return tuple(_immutable(e) for e in data)
    return data
