## evil-minions

Инструмент нагрузочного тестирования [Salt](https://github.com/saltstack/salt) в связке с [Uyuni](https://www.uyuni-project.org/) и [SUSE Manager](https://www.suse.com/products/suse-manager/).

### Назначение

Подменяется точка входа `salt-minion`: один реальный minion остаётся эталоном, дополнительно поднимается заданное число логических minion с префиксом id (по умолчанию `evil-*`). Для master они выглядят как отдельные узлы. Ответы на команды вне встроенных исключений воспроизводятся по заранее снятому с эталона трафику (baseline).

### Форк от upstream

База: `uyuni-project/evil-minions`. В данном репозитории:

- Salt 3007.x, onedir, запуск через `/opt/saltstack/salt/bin/python3.10`;
- совместимость с Python 3.13 (отказ от `distutils` в пользу `shutil.which` и аналогов там, где нужно);
- перехват через актуальный `salt.channel.client`;
- доработки асинхронных callback;
- таргетинг `glob` для шаблонов вида `evil-*`;
- один ZMQ PUSH на процесс при пересылке событий в прокси (вместо сокета на сообщение);
- `--log-level`, уровень наследуется в дочерних процессах (`EVIL_MINIONS_LOG_LEVEL`);
- число процессов Hydra: из `--processes` либо авто от `--count` и `cpu_count()` (верхняя граница 56);
- интервал ожидания baseline в `mimic()`: из `--mimic-poll` либо авто от `--count`;
- корректный выход из `mimic()` при стартовом вызове с `fun is None`.

Upstream без этих изменений: `uyuni-project/evil-minions`.

### Установка

**RPM (SUSE)** — при необходимости заменить URL репозитория под дистрибутив:

```bash
zypper addrepo https://download.opensuse.org/repositories/systemsmanagement:/sumaform:/tools/openSUSE_Leap_15.0/systemsmanagement:sumaform:tools.repo
zypper install evil-minions
```

**Исходники (Debian/Ubuntu и др.)**:

```bash
git clone https://github.com/moio/evil-minions.git
cd evil-minions
sudo apt-get install -y python3-msgpack python3-zmq python3-tornado
```

### systemd

```bash
sudo mkdir -p /etc/systemd/system/salt-minion.service.d
sudo cp override.conf /etc/systemd/system/salt-minion.service.d/override.conf
# заменить /path/to/evil-minions на каталог установки
sudo systemctl daemon-reload
sudo systemctl restart salt-minion
```

Пример `ExecStart` для onedir (пути и `--count` правятся под среду):

```ini
[Service]
ExecStart=
ExecStart=/opt/saltstack/salt/bin/python3.10 /path/to/evil-minions/evil-minions --count=100 --ramp-up-delay=0 --slowdown-factor=0.0 --log-level=INFO
WorkingDirectory=/path/to/evil-minions
```

В репозитории `override.conf` содержит тот же шаблон; фактическое число minion задаётся в `ExecStart`. Справка по ключам: `evil-minions --help`.

### Параметры запуска

| Параметр | Назначение |
|----------|------------|
| `--count` | Число симулируемых minion (дефолт скрипта: 100). |
| `--id-prefix`, `--id-offset` | Префикс и смещение id. |
| `--ramp-up-delay` | Задержка между стартами соседних голов, сек. |
| `--slowdown-factor` | Множитель задержек при проигрывании цепочки (0 — без замедления относительно записанных интервалов). |
| `--random-slowdown-factor` | Случайная добавка к `slowdown-factor` (доля, 0–100). |
| `--processes` | Число процессов Hydra; иначе авто от `--count` и CPU. |
| `--mimic-poll` | Интервал опроса baseline в `mimic()`, сек.; иначе авто от `--count`. |
| `--keysize` | Размер ключей minion, бит (дефолт 2048). |
| `--log-level` | `DEBUG` … `CRITICAL`, дефолт `INFO`. |

### Проверка

```bash
salt '*' test.ping
salt 'evil-*' test.ping
```

Команда без baseline у эталонного minion: ответ с ошибкой до первого успешного выполнения на реальном minion с тем же `fun`/аргументами.

### Ограничения

- Транспорт: ZeroMQ.
- Таргетинг: `glob`, список id, точное совпадение id; compound и прочее — не заявлено.
- Неполная эмуляция: `mine`, `beacon`, часть сценариев `state.sls` / concurrency.
- Uyuni: без Action Chains и ряда специфичных функций.
- Масштаб на одном хосте: рост `--count` линейно увеличивает число полноценных клиентских сессий (сеть, CPU, крипта). Для больших значений — несколько узлов или снижение `--count`.
