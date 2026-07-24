#!/usr/bin/env python3
"""
Roblox Luau Minifier
====================

Превращает .lua / .luau файл в одну строку, удаляя все комментарии
и лишние пробелы, но сохраняя 100% семантики.

Как пользоваться:
    python minifier.py input.lua              # печатает минифицированный код в stdout
    python minifier.py input.lua -o out.lua   # пишет результат в файл
    python minifier.py input.lua --check      # дополнительно проверить вывод через luau-compile

Идея работы:
    1. Токенайзер режет исходник на токены (комментарии, строки, числа, идентификаторы, операторы).
    2. Минифаер проходит по токенам и собирает их в одну строку, вставляя пробел / ';'
       только там, где без них изменится лексика или синтаксис.

Никаких регулярок "удалить все пробелы" — это ломает строки и комментарии.
Только честный токенайзер.
"""

import sys
import argparse
from pathlib import Path


# ---------------------------------------------------------------------------
# ТИПЫ ТОКЕНОВ
# ---------------------------------------------------------------------------
# Каждому токену присваивается один из этих типов. Тип нужен, чтобы минифаер
# мог решить, нужен ли пробел между соседними токенами.

T_COMMENT = "COMMENT"   # -- ... или --[[ ... ]]
T_STRING  = "STRING"    # "..." '...' [[...]] [==[...]==]
T_NUMBER  = "NUMBER"    # 1, 1.5, 0x1A, 0b101, 1_000, 1e5, 0x1.8p3
T_IDENT   = "IDENT"     # идентификаторы и ключевые слова (and, local, end, ...)
T_OP      = "OP"        # операторы и пунктуация: + - * / .. :: -> ( ) [ ] { } ...
T_EOF     = "EOF"       # конец файла


class Token:
    """Один токен с типом и исходным текстом."""
    __slots__ = ("type", "value")

    def __init__(self, type_, value):
        self.type = type_
        self.value = value

    def __repr__(self):
        return f"Token({self.type}, {self.value!r})"


# ---------------------------------------------------------------------------
# КЛЮЧЕВЫЕ СЛОВА LUAU
# ---------------------------------------------------------------------------
# Нужны, чтобы отличать "идентификатор могущий заканчивать выражение" (var)
# от "ключевого слова заканчивающего инструкцию" (end, then, return, ...).
# После ключевых слов, заканчивающих инструкцию, ';' не нужен.

KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for",
    "function", "if", "in", "local", "nil", "not", "or", "repeat",
    "return", "then", "true", "until", "while",
    # Luau-расширения:
    "continue", "export", "type",
}

# Ключевые слова, после которых может начинаться новое выражение/инструкция,
# но которые НЕ могут быть "хвостом" вызова функции.
# Это безопасные слова — перед ними ';' ставить не надо, а перед `(`, `{`, строкой — надо.
# Используется в правиле вставки ';'.

# ---------------------------------------------------------------------------
# ОПЕРАТОРЫ LUAU (от длинных к коротким)
# ---------------------------------------------------------------------------
# Важен порядок: сначала пытаемся сопоставить более длинные ('..' до '.', '...' до '..'),
# иначе токенайзер откусил бы '.' от '..' и т.п.

LUAU_OPERATORS = [
    # 3 символа
    "...", ">>>", "..=",  # varargs, logical right shift, concat-assign
    # 2 символа
    "..", "::", "->", "==", "~=", "<=", ">=", "//",
    "+=", "-=", "*=", "/=", "%=", "^=", "<<", ">>",
    "&=", "|=", "^=",     # составные присваивания для битовых операций (Luau)
    # 1 символ
    "+", "-", "*", "/", "%", "^", "#", "&", "|", "~",
    "=", "<", ">", "(", ")", "{", "}", "[", "]",
    ";", ":", ",", ".", "?",
]


# ---------------------------------------------------------------------------
# ТОКЕНАЙЗЕР
# ---------------------------------------------------------------------------

def tokenize(src):
    """
    Разбивает исходный Luau-код на список токенов.
    Возвращает список Token, заканчивающийся токеном T_EOF.

    Токенайзер "жадный": всегда старается сопоставить самый длинный токен.
    Это важно для '..' vs '...' vs '.', '::' vs ':' и т.д.
    """
    tokens = []
    i = 0
    n = len(src)

    while i < n:
        c = src[i]

        # --- Пробелы и переводы строк: просто пропускаем ---
        if c in " \t\r\n\f\v":
            i += 1
            continue

        # --- Комментарии: --... (строчный) или --[[...]] (блочный) ---
        if c == "-" and i + 1 < n and src[i + 1] == "-":
            # Проверяем, блочный ли это комментарий: --[=*[ ... ]=*]
            if i + 2 < n and src[i + 2] == "[":
                eq_count, j = _match_long_bracket_open(src, i + 2)
                if j is not None:
                    # Это блочный комментарий. Ищем закрывающую ]=*].
                    close = "]" + ("=" * eq_count) + "]"
                    end = src.find(close, j)
                    if end == -1:
                        # Незакрытый комментарий — съедаем всё до конца файла.
                        tokens.append(Token(T_COMMENT, src[i:]))
                        i = n
                    else:
                        tokens.append(Token(T_COMMENT, src[i:end + len(close)]))
                        i = end + len(close)
                    continue
            # Обычный строчный комментарий: до конца строки.
            j = i
            while j < n and src[j] != "\n":
                j += 1
            tokens.append(Token(T_COMMENT, src[i:j]))
            i = j
            continue

        # --- Короткие строки: "..." и '...' ---
        if c == '"' or c == "'":
            j = _read_short_string(src, i, n)
            tokens.append(Token(T_STRING, src[i:j]))
            i = j
            continue

        # --- Длинные строки: [[...]] и [==[...]==] ---
        if c == "[":
            eq_count, j = _match_long_bracket_open(src, i)
            if j is not None:
                close = "]" + ("=" * eq_count) + "]"
                end = src.find(close, j)
                if end == -1:
                    # Незакрытая длинная строка — берём до конца (лучше что-то, чем зациклиться).
                    tokens.append(Token(T_STRING, src[i:]))
                    i = n
                else:
                    tokens.append(Token(T_STRING, src[i:end + len(close)]))
                    i = end + len(close)
                continue
            # Иначе '[' — это оператор индексации. Падаем в общий разбор операторов.

        # --- Числа ---
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            j = _read_number(src, i, n)
            tokens.append(Token(T_NUMBER, src[i:j]))
            i = j
            continue

        # --- Идентификаторы и ключевые слова ---
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            tokens.append(Token(T_IDENT, src[i:j]))
            i = j
            continue

        # --- Операторы / пунктуация ---
        matched = False
        for op in LUAU_OPERATORS:
            if src.startswith(op, i):
                tokens.append(Token(T_OP, op))
                i += len(op)
                matched = True
                break
        if not matched:
            # Неизвестный символ — в Luau такого быть не должно, но мы его не теряем,
            # чтобы дальше можно было диагностировать проблему.
            tokens.append(Token(T_OP, c))
            i += 1

    tokens.append(Token(T_EOF, ""))
    return tokens


def _match_long_bracket_open(src, i):
    """
    Проверяет, начинается ли в позиции i открывающая длинная скобка вида [==[.
    Возвращает (eq_count, j) где j — индекс после открывающей '['.
    Если это не длинная скобка, возвращает (0, None).
    """
    n = len(src)
    if i >= n or src[i] != "[":
        return 0, None
    j = i + 1
    eq_count = 0
    while j < n and src[j] == "=":
        eq_count += 1
        j += 1
    if j < n and src[j] == "[":
        return eq_count, j + 1
    return 0, None


def _read_short_string(src, i, n):
    r"""
    Читает короткую строку начиная с позиции i (где src[i] — это ' или ").
    Возвращает индекс после закрывающей кавычки.
    Корректно обрабатывает экранирования:
      \\ \" \' \a \b \f \n \r \t \v \xNN \ddd \u{...} \z
    Спец-случай — '\z' (skip whitespace): после него лексер пропускает все
    пробелы и переводы строк до следующего непробельного символа,
    и строка продолжается дальше. Поэтому мы тоже должны пропустить пробелы.
    """
    quote = src[i]
    j = i + 1
    while j < n:
        c = src[j]
        if c == "\\":
            # Смотрим, что за экранирование.
            if j + 1 < n and src[j + 1] == "z":
                # \z пропускает все пробельные символы после себя.
                j += 2
                while j < n and src[j] in " \t\r\n\f\v":
                    j += 1
                continue
            # Любое другое экранирование: пропускаем 2 символа.
            # (Для \xNN, \ddd, \u{...} этого достаточно — мы не валидируем содержимое,
            #  лишь бы не преждевременно "закрыть" строку кавычкой, которая является
            #  частью escape-последовательности.)
            j += 2
            continue
        if c == quote:
            return j + 1
        if c == "\n":
            # В Luau короткая строка не может содержать сырой перевод строки
            # (кроме случая \z, который обработан выше). Считаем строку закончившейся.
            return j
        j += 1
    return j  # незакрытая строка — берём до конца


def _read_number(src, i, n):
    """
    Читает числовой литерал Luau. Поддерживаются:
      - десятичные:        1, 1.5, .5, 1e5, 1.5e-3, 1_000_000
      - шестнадцатеричные: 0x1A, 0xFF, 0x1.8p3 (hex float)
      - двоичные (Luau):   0b1010, 0b1010_0011
      - разделители '_':   1_000_000, 0xFF_FF
    Возвращает индекс после числа.
    """
    start = i
    c = src[i]

    if c == "0" and i + 1 < n and src[i + 1] in "xX":
        # Шестнадцатеричное число (в т.ч. hex float с p-экспонентой).
        j = i + 2
        while j < n and (src[j] in "0123456789abcdefABCDEF._"):
            j += 1
        # Экспонента для hex float: p / P
        if j < n and src[j] in "pP":
            j += 1
            if j < n and src[j] in "+-":
                j += 1
            while j < n and (src[j].isdigit() or src[j] == "_"):
                j += 1
        return j

    if c == "0" and i + 1 < n and src[i + 1] in "bB":
        # Двоичное число (Luau).
        j = i + 2
        while j < n and (src[j] in "01_"):
            j += 1
        return j

    # Десятичное число.
    j = i
    while j < n and (src[j].isdigit() or src[j] == "_"):
        j += 1
    if j < n and src[j] == ".":
        j += 1
        while j < n and (src[j].isdigit() or src[j] == "_"):
            j += 1
    if j < n and src[j] in "eE":
        j += 1
        if j < n and src[j] in "+-":
            j += 1
        while j < n and (src[j].isdigit() or src[j] == "_"):
            j += 1
    return j


# ---------------------------------------------------------------------------
# МИНИФАЕР
# ---------------------------------------------------------------------------

def _need_space(left, right):
    """
    Решает, нужен ли пробел между двумя соседними токенами (после удаления комментариев).
    Возвращает True, если без пробела слипание изменит лексику или синтаксис.

    Логика консервативная: лучше поставить лишний пробел, чем сломать код.
    """
    if left.type == T_EOF or right.type == T_EOF:
        return False

    lt, rt = left.type, right.type
    lv, rv = left.value, right.value

    # 1) IDENT IDENT — например `local x`. Без пробела `localx` — один идентификатор.
    if lt == T_IDENT and rt == T_IDENT:
        return True

    # 2) IDENT NUMBER — `local 5` -> `local5` (один идентификатор). Нужен пробел.
    if lt == T_IDENT and rt == T_NUMBER:
        return True

    # 3) NUMBER IDENT — `0xFF a` -> `0xFFa` (другое число). Нужен пробел.
    #    Также `5 else` -> `5else` ломает парсер (пытается разобрать `5e` как экспоненту).
    if lt == T_NUMBER and rt == T_IDENT:
        return True

    # 4) NUMBER NUMBER — валидного случая нет, ставим пробел для безопасности.
    if lt == T_NUMBER and rt == T_NUMBER:
        return True

    # 5) NUMBER, а дальше оператор, начинающийся с '.': '..' или '.'.
    #    Иначе `1 .. 2` превратится в `1..2`, что парсер прочитает как `1.` `..` `2`
    #    (другая семантика для некоторых случаев).
    if lt == T_NUMBER and rt == T_OP and rv[0] == ".":
        return True

    # 6) '-' '-' слипается в комментарий '--'.
    if lt == T_OP and lv == "-" and rt == T_OP and rv == "-":
        return True

    # 7) '[' '[' или '[' '=' слипается в длинную строку '[[' / '[=['.
    if lt == T_OP and lv == "[" and rt == T_OP and rv and rv[0] == "[":
        return True

    # 8) '..' '.' слипается в '...' (varargs вместо двух конкатенаций-с-индексом).
    if lt == T_OP and lv == ".." and rt == T_OP and rv == ".":
        return True

    # 9) '..' '..=' слипается в '...=' (плохо). Обрабатываем как часть предыдущего правила
    #    (rv[0] == '.'), оно уже покроет '..='.

    # 10) '..' за ним STRING начинается с '[' — нет проблем, '..' уже完整, строка не сольётся.
    #     Но если STRING это '[[...]]', то '.. [[' может слипнуться? Нет: '..' оператор, '[['
    #     начинается новая длинная строка — парсер разберёт правильно.

    # 11) '=' '=' слипается в '==' (равенство вместо двух присваиваний).
    #     Двух подряд присваиваний в валидном коде не бывает, но на всякий случай:
    if lt == T_OP and lv == "=" and rt == T_OP and rv == "=":
        return True

    # 12) '<' '<' слипается в '<<' (сдвиг). В Luau это разные операторы.
    if lt == T_OP and lv == "<" and rt == T_OP and rv == "<":
        return True

    # 13) '>' '>' слипается в '>>'.
    if lt == T_OP and lv == ">" and rt == T_OP and rv == ">":
        return True

    # 14) '>' '>=' слипается в '>>='? В Luau нет такого. Но всё равно опасно, ставим пробел.
    if lt == T_OP and lv == ">" and rt == T_OP and rv == ">=":
        return True

    # 15) '/' '/' слипается в '//' (целочисленное деление).
    if lt == T_OP and lv == "/" and rt == T_OP and rv == "/":
        return True

    # 16) '/' '//=' слипается в '//='.
    if lt == T_OP and lv == "/" and rt == T_OP and rv == "//=":
        return True

    # 17) '~' '=' слипается в '~='.
    if lt == T_OP and lv == "~" and rt == T_OP and rv == "=":
        return True

    # 18) ':' ':' слипается в '::' (type assertion).
    if lt == T_OP and lv == ":" and rt == T_OP and rv == ":":
        return True

    # 19) '-' '>' отдельно: `-` `>` должно стать `->`? Наоборот, НЕ должно.
    #     Если в исходнике было `-` `>` (что не валидно в Luau), не сольём.
    if lt == T_OP and lv == "-" and rt == T_OP and rv == ">":
        return True

    return False


def _need_semicolon(left, right):
    """
    Решает, нужно ли сохранить ';' перед right-токеном.
    ';' в Lua необязательна, КРОМЕ случая, когда следующая инструкция начинается
    с '(', строки или таблицы '{' — иначе парсер подумает, что это аргументы вызова.

    Пример:
        f()              -- одна инструкция: вызов f
        (g)()            -- вторая инструкция: вызов g
        f() (g)()        -- БЕЗ ';': ОДНА инструкция "вызови f, потом результат с g, потом результат"
        f(); (g)()       -- С ';': ДВЕ инструкции (как и задумано)

    Поэтому мы НЕ удаляем ';', если за ней идёт '(', строка или '{'.
    """
    if left.type != T_OP or left.value != ";":
        return False
    if right.type == T_EOF:
        return False
    if right.type == T_OP and right.value in ("(", "{"):
        return True
    if right.type == T_STRING:
        return True
    return False


def _normalize_short_string(value):
    r"""
    Нормализует короткую строку (токен Luau), удаляя из неё `\z` + последующий
    whitespace. В Luau `\z` skip whitespace — пропускает все пробелы и переводы
    строк до следующего непробельного символа. То есть `"a\z\n    b"` эквивалентно
    `"ab"`. Минифаер применяет это, чтобы в выходной строке не осталось
    физических переносов строк.

    Если после нормализации строка стала содержать '"', а исходно не содержала,
    мы вернём строку с одинарной кавычкой — это безопасно, т.к. ``'`` в Luau
    тоже валидный разделитель. (На самом деле `\z` не добавляет кавычек, поэтому
    этот случай не возникает — но проверка ничего не стоит.)

    Возвращает новый токен-строку.
    """
    if "\\z" not in value:
        return value  # оптимизация: чаще всего \z вообще не встречается

    # Проходим по строке символ за символом, разворачивая \z.
    # value начинается с кавычки (одинарной или двойной).
    if not value or value[0] not in "\"'":
        return value
    quote = value[0]
    out = [quote]
    i = 1
    n = len(value)
    while i < n - 1:  # последняя кавычка — закрывающая
        c = value[i]
        if c == "\\" and i + 1 < n:
            nxt = value[i + 1]
            if nxt == "z":
                # \z skip whitespace: пропускаем все пробельные символы
                # после \z. Сам \z в output не пишем.
                i += 2
                while i < n - 1 and value[i] in " \t\r\n\f\v":
                    i += 1
                continue
            else:
                # Любое другое escape: пишем как есть.
                out.append(value[i:i + 2])
                i += 2
                continue
        out.append(c)
        i += 1
    out.append(quote)  # закрывающая кавычка
    return "".join(out)


def _long_string_to_short(value):
    """
    Превращает токен длинной строки `[[...]]` / `[==[...]==]` в токен
    обычной короткой строки "...". Возвращает новую строку-токен или None,
    если преобразование невозможно (например, внутренняя структура некорректна).

    Зачем: длинные строки могут содержать сырые переводы строк, что мешает
    получить по-настоящему однострочный минифицированный вывод. Преобразование
    в короткую строку с `\n` делает вывод однострочным.

    Пример:
        [[hello
        world]]  ->  "hello\nworld"
    """
    # Определяем уровень длинной скобки.
    # value начинается с '[' за ним '='* за ним '['.
    if not value.startswith("["):
        return None
    j = 1
    while j < len(value) and value[j] == "=":
        j += 1
    if j >= len(value) or value[j] != "[":
        return None
    eq_count = j - 1
    open_bracket = "[" + ("=" * eq_count) + "["
    close_bracket = "]" + ("=" * eq_count) + "]"
    if not value.startswith(open_bracket) or not value.endswith(close_bracket):
        return None
    if len(value) < len(open_bracket) + len(close_bracket):
        return None

    # Содержимое между скобками.
    content = value[len(open_bracket):-len(close_bracket)]

    # Luau убирает первый перевод строки сразу после `[[` (если он есть).
    if content.startswith("\n"):
        content = content[1:]
    elif content.startswith("\r\n"):
        content = content[2:]
    elif content.startswith("\r"):
        content = content[1:]

    # Теперь экранируем содержимое для короткой строки.
    # Используем двойные кавычки — если в содержимом много ', и нет ".
    # Иначе одинарные. Если есть и " и ' — выбираем " и экранируем ".
    has_double = '"' in content
    has_single = "'" in content
    if not has_double:
        quote = '"'
        escaped = content.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace('"', '\\"')
    elif not has_single:
        quote = "'"
        escaped = content.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("'", "\\'")
    else:
        # Есть и " и '. Используем " и экранируем только её.
        quote = '"'
        escaped = (
            content
            .replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace('"', '\\"')
        )
    return quote + escaped + quote


def minify(src, convert_long_strings=True):
    """
    Главная функция минификации. Принимает исходный код, возвращает минифицированный.

    Параметры:
        convert_long_strings — если True, длинные строки `[[...]]` преобразуются
            в обычные `"..."` с `\n`, чтобы результат был по-настоящему
            однострочным. Иначе длинные строки остаются как есть.
    """
    tokens = tokenize(src)

    # 1) Выбрасываем комментарии. Строки/числа/иденты/операторы остаются.
    stripped = [t for t in tokens if t.type != T_COMMENT]

    # 2) Собираем результат. Параллельно отслеживаем глубину вложенности таблиц.
    #    ';' внутри таблицы `{...}` — это разделитель элементов (наряду с ','),
    #    его удалять НЕЛЬЗЯ. ';' вне таблицы — это statement separator, его
    #    можно удалять (кроме случая перед '(', '{' или строкой — см. _need_semicolon).
    out = []
    table_depth = 0  # сколько открытых '{' еще не закрыто
    paren_depth = 0  # сколько открытых '(' еще не закрыто (для информативности)

    for k in range(len(stripped)):
        cur = stripped[k]
        if cur.type == T_EOF:
            break

        # Обработка ';'.
        if cur.type == T_OP and cur.value == ";":
            if table_depth > 0:
                # Внутри таблицы: ';' это разделитель элементов, оставляем как есть.
                pass
            else:
                # Вне таблицы: ';' это statement separator.
                # Удаляем, если за ней не идёт '(', '{' или строка.
                nxt = stripped[k + 1] if k + 1 < len(stripped) else Token(T_EOF, "")
                if not _need_semicolon(cur, nxt):
                    continue  # пропускаем эту ';'

        # Преобразование длинной строки в короткую (если включено).
        if convert_long_strings and cur.type == T_STRING and cur.value.startswith("["):
            converted = _long_string_to_short(cur.value)
            if converted is not None:
                cur = Token(T_STRING, converted)

        # Нормализация коротких строк: удаляем `\z` + whitespace,
        # чтобы в выходной строке не осталось физических переносов.
        if cur.type == T_STRING:
            cur = Token(T_STRING, _normalize_short_string(cur.value))

        # Между предыдущим и текущим токеном — пробел если нужно.
        if out:
            prev_token = _last_real_token(stripped, k)
            if prev_token is not None and _need_space(prev_token, cur):
                out.append(" ")

        # Обновляем глубину вложенности ПОСЛЕ всех проверок, чтобы текущий токен
        # видел корректное состояние (для ';' это важно — оно проверяется до обновления).
        if cur.type == T_OP:
            if cur.value == "{":
                table_depth += 1
            elif cur.value == "}":
                table_depth = max(0, table_depth - 1)
            elif cur.value == "(":
                paren_depth += 1
            elif cur.value == ")":
                paren_depth = max(0, paren_depth - 1)

        out.append(cur.value)

    return "".join(out) + "\n"


def _last_real_token(stripped, k):
    """
    Возвращает предыдущий токен перед позицией k, ПРОПУСКАЯ удалённые ';'.

    Важный момент: ';' удаляется только если она стоит как statement separator
    (то есть за ней не идёт '(', '{' или строка). ';' перед '(', '{' или строкой
    сохраняется — и в этом случае её нужно учитывать как предыдущий токен
    (например, для правила _need_space).

    ';' внутри таблицы {...} — это разделитель элементов, она никогда не удаляется,
    поэтому её тоже нужно учитывать как предыдущий токен.
    """
    j = k - 1
    while j >= 0:
        t = stripped[j]
        if t.type == T_OP and t.value == ";":
            # Проверяем, будет ли эта ';' удалена.
            # Если за ней идёт '(', '{' или строка — она сохраняется, не пропускаем.
            nxt = stripped[j + 1] if j + 1 < len(stripped) else Token(T_EOF, "")
            if _need_semicolon(t, nxt):
                return t  # сохраняется, возвращаем как предыдущий
            # Иначе — удаляется, пропускаем.
            j -= 1
            continue
        return t
    return None


# ---------------------------------------------------------------------------
# ВТОРИЧНАЯ ПРОВЕРКА: ТОКЕН-ЭКВИВАЛЕНТНОСТЬ
# ---------------------------------------------------------------------------

def _strip_meaningful(tokens, normalize_long_strings=False, normalize_z_escape=False,
                       ignore_semicolons=True):
    r"""
    Возвращает список (type, value) для всех значимых токенов (без комментариев и EOF).

    Если normalize_long_strings=True, длинные строки `[[...]]` заменяются на
    эквивалентные короткие `"..."`. Это нужно, чтобы сравнивать токен-эквивалентность
    когда minifier применил преобразование длинных строк в короткие.

    Если normalize_z_escape=True, из коротких строк удаляется `\z` + whitespace
    (как это делает minifier). Без этого сравнение считало бы `"a\z\nb"` и `"ab"`
    разными строками, хотя семантически они идентичны.

    Если ignore_semicolons=True (по умолчанию), ';' токены не включаются в результат.
    ';' в Lua необязательна (statement separator) и minifier её удаляет — поэтому
    для проверки эквивалентности её надо игнорировать.
    """
    result = []
    for t in tokens:
        if t.type in (T_COMMENT, T_EOF):
            continue
        if ignore_semicolons and t.type == T_OP and t.value == ";":
            continue
        v = t.value
        if t.type == T_STRING:
            if normalize_long_strings and v.startswith("["):
                converted = _long_string_to_short(v)
                if converted is not None:
                    v = converted
            if normalize_z_escape:
                v = _normalize_short_string(v)
        result.append((t.type, v))
    return result


def check_token_equivalence(original_src, minified_src, normalize=True):
    r"""
    Сравнивает набор значимых токенов исходного и минифицированного кода.
    Возвращает (True, None) если эквивалентны, иначе (False, diff_message).

    Это очень сильная проверка: если у оригинала и минифицированного одинаковые
    токены в том же порядке — их семантика идентична (Lua-парсер даст то же AST).

    normalize: если True (по умолчанию), перед сравнением строки нормализуются:
    длинные преобразуются в короткие, `\z` + whitespace удаляется. Это отражает
    то, что делает minifier. Сравнение всё ещё строгое: содержимое строк должно
    совпадать после нормализации.
    """
    orig_tokens = _strip_meaningful(
        tokenize(original_src),
        normalize_long_strings=normalize,
        normalize_z_escape=normalize,
    )
    mini_tokens = _strip_meaningful(
        tokenize(minified_src),
        normalize_long_strings=normalize,
        normalize_z_escape=normalize,
    )

    if len(orig_tokens) != len(mini_tokens):
        return False, (
            f"Количество токенов отличается: оригинал={len(orig_tokens)}, "
            f"минифицированный={len(mini_tokens)}"
        )

    for i, (a, b) in enumerate(zip(orig_tokens, mini_tokens)):
        if a != b:
            return False, (
                f"Токен #{i} отличается:\n"
                f"  оригинал:         {a}\n"
                f"  минифицированный: {b}"
            )

    return True, None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Roblox Luau minifier — превращает .lua/.luau файл в одну строку.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python minifier.py script.lua\n"
            "  python minifier.py script.lua -o min.lua\n"
            "  python minifier.py script.lua --check\n"
            "  python minifier.py script.lua --check --luau /path/to/luau-compile\n"
        ),
    )
    parser.add_argument("input", help="Путь к .lua/.luau файлу (или .txt с luau-кодом)")
    parser.add_argument("-o", "--output", help="Куда сохранить результат (по умолчанию — в stdout)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Проверить минифицированный вывод через luau-compile и токен-эквивалентность",
    )
    parser.add_argument(
        "--luau",
        default=_detect_luau_compile(),
        help="Путь к luau-compile (для --check). По умолчанию ищется в PATH.",
    )
    parser.add_argument(
        "--no-token-check",
        action="store_true",
        help="Пропустить проверку токен-эквивалентности (только luau-compile)",
    )
    args = parser.parse_args()

    # Читаем исходник.
    src_path = Path(args.input)
    if not src_path.is_file():
        print(f"Ошибка: файл не найден — {args.input}", file=sys.stderr)
        sys.exit(2)

    src = src_path.read_text(encoding="utf-8")

    # Минифицируем.
    try:
        minified = minify(src)
    except Exception as e:
        print(f"Ошибка при минификации: {e}", file=sys.stderr)
        sys.exit(1)

    # Сохраняем или печатаем результат.
    if args.output:
        Path(args.output).write_text(minified, encoding="utf-8")
    else:
        sys.stdout.write(minified)

    # Дополнительная проверка, если попросили.
    if args.check:
        ok = True

        # 1) Проверка токен-эквивалентности.
        if not args.no_token_check:
            equiv, msg = check_token_equivalence(src, minified)
            if equiv:
                print("[OK] Токен-эквивалентность: оригинал и минифицированный код "
                      "дают одинаковый поток токенов.", file=sys.stderr)
            else:
                ok = False
                print(f"[FAIL] Токен-эквивалентность нарушена:\n{msg}", file=sys.stderr)

        # 2) Проверка через luau-compile --only-parse.
        if args.luau:
            import subprocess
            import tempfile
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".lua", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(minified)
                tmp_path = tmp.name
            try:
                proc = subprocess.run(
                    [args.luau, "--only-parse", tmp_path],
                    capture_output=True, text=True,
                )
                if proc.returncode == 0:
                    print(f"[OK] luau-compile: синтаксис минифицированного кода корректен.",
                          file=sys.stderr)
                else:
                    ok = False
                    print(f"[FAIL] luau-compile нашёл синтаксическую ошибку:\n"
                          f"{proc.stderr.strip()}", file=sys.stderr)
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        else:
            print("[WARN] luau-compile не найден — пропуск проверки синтаксиса. "
                  "Укажи путь через --luau.", file=sys.stderr)

        # Краткая статистика.
        orig_size = len(src.encode("utf-8"))
        mini_size = len(minified.encode("utf-8"))
        ratio = (mini_size / orig_size * 100) if orig_size else 0
        print(f"[STAT] Размер: {orig_size} -> {mini_size} байт ({ratio:.1f}%)",
              file=sys.stderr)

        sys.exit(0 if ok else 1)


def _detect_luau_compile():
    """Ищет luau-compile в PATH. Возвращает путь или None."""
    import shutil
    return shutil.which("luau-compile")


if __name__ == "__main__":
    main()
