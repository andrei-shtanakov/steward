# Host-local расписание сбора `approval-facts` (Stage A0)

Проза вынесена сюда из `com.steward.approval-facts.plist.template` не ради
аккуратности: **XML-комментарий не может содержать `--`**, а инструкция полна
`--workspace-root`, `--check` и `sed -e`. Пока текст лежал внутри комментария,
шаблон и получаемый из него plist были невалидным XML — `launchctl load` не
принял бы его вовсе, и сбор не запускался бы никогда. Регрессию сторожит
`test_plist_template_is_valid_xml`.

Шаблон host-local расписания для сбора `approval-facts/v2` (Stage A0).

  НЕ УСТАНАВЛИВАЕТСЯ РЕПОЗИТОРИЕМ. Это host-local prerequisite: путь к
  воркспейсу и к логам у каждой машины свой, а решение «пусть на этом ноутбуке
  что-то просыпается» принимает владелец машины, не PR.

  Почему локально, а не в Actions. Продюсер пишет
  `<checkout>/.steward/approval_facts.jsonl`, а потребитель читает файл из
  чекаута. У CI чекаутов флота нет по построению, и артефакт прогона до
  потребителя не доходит. Это не откат перехода launchd→CI: тогда в Actions
  уводили проверки, которым нужен был только GitHub.

  Возражение против локального планировщика было одно и остаётся верным —
  «выключенный ноутбук не способен сам сообщить, что launchd не сработал».
  Здесь на него отвечает сам артефакт: в заголовке бандла лежит `valid_until`
  (lease 24 ч), поэтому молчание расписания **обнаружимо при чтении**. Оно не
  сообщается само — обнаружить его должен тот, кто читает; до появления
  потребителя это делается глазами:

      uv run python scripts/collect_approval_facts.py --workspace-root <WS> --check

  Период 6 часов при lease 24 часа. Запас не косметический: у расписаний
  GitHub измеренный дрейф очереди 40–70 мин (см. комментарий в
  `.github/workflows/impresario-contract-drift.yml`), у launchd сна машины —
  неограниченный, поэтому период обязан покрывать `старт + прогон` с
  многократным перекрытием, а не «формально меньше lease».

  После установки обязательно убедиться, что запуск вообще происходит: plist,
  который не стартует, ничем себя не выдаёт, кроме протухшего через сутки бандла.

      launchctl kickstart -k gui/$(id -u)/com.steward.approval-facts
      tail -n 20 @LOG_DIR@/approval-facts.err.log

  Плейсхолдеры записаны как `@ИМЯ@`, а не `<ИМЯ>`, и это не стиль: внутри XML
  угловые скобки пришлось бы экранировать (`&lt;ИМЯ&gt;`), и тогда `sed`,
  ищущий `<ИМЯ>`, не нашёл бы ничего — plist установился бы с литеральными
  плейсхолдерами, агент загрузился бы и не запустился ни разу.

  **Ограничение путей.** Подстановка идёт сырым `sed` сразу в XML и в
  shell-команду, поэтому путь не должен содержать `&`, `<`, `>`, кавычек и
  переводов строк: `&` порвёт XML, кавычка — команду в `ProgramArguments`.
  Названо явно, а не обойдено: экранировать оба слоя одной строкой `sed`
  честно нельзя, а молчаливый отказ здесь стоил бы дороже — plist установился
  бы, а сбор не запускался бы никогда. Если путь такой, plist правится руками.

  Установка. `~/Library/LaunchAgents` создаётся явно: на свежей учётной записи
  его может не быть, и перенаправление вывода упало бы с `No such file or
  directory` — plist не появился бы вовсе. Пути задаются переменными и
  подставляются один раз — иначе
  `@LOG_DIR@` остаётся литералом вне `sed`, и `mkdir` создаёт каталог с таким
  именем, а логи пишутся не туда, куда потом смотрит проверка:

      WS="/Users/you/labs/all_ai_orchestrators"
      STEWARD="$WS/steward"
      LOGS="$HOME/Library/Logs/steward"

      mkdir -p "$LOGS" ~/Library/LaunchAgents
      sed -e "s|@WORKSPACE_ROOT@|$WS|g" \
          -e "s|@STEWARD_ROOT@|$STEWARD|g" \
          -e "s|@UV_BIN@|$(command -v uv)|g" \
          -e "s|@LOG_DIR@|$LOGS|g" \
          "$STEWARD/scripts/com.steward.approval-facts.plist.template" \
          > ~/Library/LaunchAgents/com.steward.approval-facts.plist
      launchctl load ~/Library/LaunchAgents/com.steward.approval-facts.plist

  Затем убедиться, что запуск вообще происходит: plist, который не стартует,
  ничем себя не выдаёт, кроме протухшего через сутки бандла.

      launchctl kickstart -k "gui/$(id -u)/com.steward.approval-facts"
      tail -n 20 "$LOGS/approval-facts.err.log"

  Снятие:

      launchctl unload ~/Library/LaunchAgents/com.steward.approval-facts.plist
      rm ~/Library/LaunchAgents/com.steward.approval-facts.plist
