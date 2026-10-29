
---

## 2. `README.ru.md`

```markdown
# hdd-health-predictor

**Предиктивный монитор отказа HDD для Linux.** Обнаруживает деградацию шпинделя
и позиционера головок за недели до того, как S.M.A.R.T. сообщит о переназначенных
секторах — отслеживая микро-тренды `Seek Error Rate`, `Calibration Retry Count`
и ошибки ATA-линка в ядре.

> English version: [README.md](README.md)

---

## Зачем

Стандартный S.M.A.R.T. предупреждает **слишком поздно** — когда сектора уже
начинают сыпаться (`Reallocated_Sector_Ct` > 0). Этот демон ловит *скрытые*
ранние сигналы:

| Атрибут | ID | Что значит |
|---|---|---|
| Raw Read Error Rate | 1 | Повторы чтения на уровне пластины |
| Seek Error Rate | 7 | Ошибки позиционирования актуатора |
| Calibration Retry Count | 11 | Повторы калибровки шпинделя/головок |
| Ошибки ATA в ядре | — | `COMRESET failed`, `soft resetting link`, `DRDY ERR` |

Считает **сглаживание Хольта-Винтерса**, **наклон линейной регрессии** и
**вторую производную (ускорение)** роста ошибок. Линейный рост — нормальный
износ. Экспоненциальный — признак скорой смерти механики.

Выход: **Health Index (0–100 %)** и **Time-to-Failure (дни)**.

---

## Возможности

- ✅ Работает как `systemd`-таймер, **раз в час** (низкая нагрузка на I/O)
- ✅ **Не будит спящий диск** (`smartctl -n standby`)
- ✅ Декодирование 48-битных raw-значений Seagate
- ✅ Автоопределение типа устройства (`-d sat` → `-d scsi` → `-d auto`)
- ✅ Ядро математики на чистом Python (работает без numpy/scipy)
- ✅ Desktop-уведомление (`notify-send`) + syslog (`logger`) + JSON-лог алертов
- ✅ Анти-спам: повторный алерт подавляется на 6 ч
- ✅ SQLite в `/var/log/hdd_pre_fail/` (не на мониторимом диске)

---

## Требования

- Linux с `systemd` ≥ 240
- Python 3.9+
- `smartmontools` (`smartctl`)
- `libnotify-bin` (`notify-send`) — опционально, есть fallback на syslog
- Права root (SMART требует)

```bash
sudo apt install smartmontools libnotify-bin python3-venv

## Установка

bash

git clone https://github.com/finnapteka/hdd-health-predictor.git
cd hdd-health-predictor
sudo ./install.sh

Установщик:

    Копирует файлы в /opt/hdd-health-predictor/

    Создаёт venv и ставит requirements.txt

    Устанавливает hdd-health-predictor.service и .timer в /etc/systemd/system/

    Включает и запускает таймер

Отредактируй /opt/hdd-health-predictor/config.yaml — какие диски мониторить.

## Конфигурация

/opt/hdd-health-predictor/config.yaml:
yaml

disks:
  - /dev/sdc
  - /dev/sdd
  - /dev/sda

interval_hours: 1
min_points: 24            # холодный старт: собираем 24 ч до алертов
hi_threshold: 50          # алерт если Health Index < 50 %
alert_cooldown_hours: 6
notify_user: sss          # desktop-пользователь для notify-send
notify_display: ":0"
db_path: /var/log/hdd_pre_fail/metrics.db
state_path: /var/log/hdd_pre_fail/state.json
alerts_log: /var/log/hdd_pre_fail/alerts.log

## Ручной запуск

bash

# снять один замер и проанализировать
sudo /opt/hdd-health-predictor/venv/bin/python \
     /opt/hdd-health-predictor/src/hdd_parser.py --config /opt/hdd-health-predictor/config.yaml

# только анализ (без сбора)
sudo /opt/hdd-health-predictor/venv/bin/python \
     /opt/hdd-health-predictor/src/trend_analyzer.py \
     --config /opt/hdd-health-predictor/config.yaml --dev sdc

## Симуляция отказа (для проверки)

bash

sudo /opt/hdd-health-predictor/venv/bin/python \
     /opt/hdd-health-predictor/tools/simulate_anomaly.py --dev sdc --days 30

Скрипт вливает экспоненциальный тренд Seek Error Rate и Calibration Retry Count
в SQLite, затем запускает анализатор — должно прийти desktop-уведомление и строка
в /var/log/hdd_pre_fail/alerts.log.

## Поддерживаемое железо

| Тип | Механический прогноз | Примечания |
|---|---|---|
| **SATA HDD (Seagate, WD, Hitachi)** | Да | Атрибуты 1/7/11 + Seagate-специфичные 187/188/193/197/198/240 |
| **SAS HDD** | Частично | Через `-d scsi`, зависит от прошивки |
| **SSD / NVMe** | Нет | Нет механических атрибутов; корректно пропускается (`ssd_skip`) |
| **USB-флешки / внешние** | Нет | Исключаются через `/sys/block/*/removable` |
| **loop / dm / md** | Нет | Виртуальные устройства, исключены |

Проверено на:
- Seagate ST2000DL003-9VT1 (Barracuda Green 2 TB) - атрибуты 1/7/187/188/193/197/198/240
- Seagate ST1000VX005-2EZ1 (SkyHawk 1 TB) - атрибуты 1/7/187/188/193/197/198

## Как это работает

text

systemd timer (раз в час)
        │
        ▼
hdd_parser.py ──► smartctl -A -n standby ──► SQLite
        │         journalctl -k (1ч)      /var/log/hdd_pre_fail/metrics.db
        ▼
trend_analyzer.py
        │  Holt-Winters + linregress + 2-я производная
        │  → Health Index, Time-to-Failure
        ▼
notifier.py ──► notify-send + logger + alerts.log

Полная архитектура — в docs/ARCHITECTURE.md.
