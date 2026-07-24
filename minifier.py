#!/usr/bin/env python3
# python minifier.py input.lua
import sys
import argparse
from pathlib import Path

T_COMMENT = "COMMENT"
T_STRING  = "STRING"
T_NUMBER  = "NUMBER"
T_IDENT   = "IDENT"
T_OP      = "OP"
T_EOF     = "EOF"

KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for",
    "function", "if", "in", "local", "nil", "not", "or", "repeat",
    "return", "then", "true", "until", "while",
    "continue", "export", "type",
}

LUAU_OPERATORS = [
    "...", ">>>", "..=",
    "..", "::", "->", "==", "~=", "<=", ">=", "//",
    "+=", "-=", "*=", "/=", "%=", "^=", "<<", ">>",
    "&=", "|=", "^=",
    "+", "-", "*", "/", "%", "^", "#", "&", "|", "~",
    "=", "<", ">", "(", ")", "{", "}", "[", "]",
    ";", ":", ",", ".", "?",
]


class Token:
    __slots__ = ("type", "value")

    def __init__(self, type_, value):
        self.type = type_
        self.value = value

    def __repr__(self):
        return f"Token({self.type}, {self.value!r})"


def tokenize(src):
    tokens = []
    i = 0
    n = len(src)

    while i < n:
        c = src[i]

        if c in " \t\r\n\f\v":
            i += 1
            continue

        if c == "-" and i + 1 < n and src[i + 1] == "-":
            if i + 2 < n and src[i + 2] == "[":
                eq_count, j = _match_long_bracket_open(src, i + 2)
                if j is not None:
                    close = "]" + ("=" * eq_count) + "]"
                    end = src.find(close, j)
                    if end == -1:
                        tokens.append(Token(T_COMMENT, src[i:]))
                        i = n
                    else:
                        tokens.append(Token(T_COMMENT, src[i:end + len(close)]))
                        i = end + len(close)
                    continue
            j = i
            while j < n and src[j] != "\n":
                j += 1
            tokens.append(Token(T_COMMENT, src[i:j]))
            i = j
            continue

        if c == '"' or c == "'":
            j = _read_short_string(src, i, n)
            tokens.append(Token(T_STRING, src[i:j]))
            i = j
            continue

        if c == "`":
            j = i + 1
            while j < n:
                cc = src[j]
                if cc == "\\" and j + 1 < n:
                    j += 2
                    continue
                if cc == "`":
                    j += 1
                    break
                if cc == "{":
                    depth = 1
                    j += 1
                    while j < n and depth > 0:
                        if src[j] == "{" and (j == 0 or src[j-1] != "\\"):
                            depth += 1
                        elif src[j] == "}" and (j == 0 or src[j-1] != "\\"):
                            depth -= 1
                        j += 1
                    continue
                j += 1
            tokens.append(Token(T_STRING, src[i:j]))
            i = j
            continue

        if c == "[":
            eq_count, j = _match_long_bracket_open(src, i)
            if j is not None:
                close = "]" + ("=" * eq_count) + "]"
                end = src.find(close, j)
                if end == -1:
                    tokens.append(Token(T_STRING, src[i:]))
                    i = n
                else:
                    tokens.append(Token(T_STRING, src[i:end + len(close)]))
                    i = end + len(close)
                continue

        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            j = _read_number(src, i, n)
            tokens.append(Token(T_NUMBER, src[i:j]))
            i = j
            continue

        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            tokens.append(Token(T_IDENT, src[i:j]))
            i = j
            continue

        matched = False
        for op in LUAU_OPERATORS:
            if src.startswith(op, i):
                tokens.append(Token(T_OP, op))
                i += len(op)
                matched = True
                break
        if not matched:
            tokens.append(Token(T_OP, c))
            i += 1

    tokens.append(Token(T_EOF, ""))
    return tokens


def _match_long_bracket_open(src, i):
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
    quote = src[i]
    j = i + 1
    while j < n:
        c = src[j]
        if c == "\\":
            if j + 1 < n and src[j + 1] == "z":
                j += 2
                while j < n and src[j] in " \t\r\n\f\v":
                    j += 1
                continue
            j += 2
            continue
        if c == quote:
            return j + 1
        if c == "\n":
            return j
        j += 1
    return j


def _read_number(src, i, n):
    c = src[i]

    if c == "0" and i + 1 < n and src[i + 1] in "xX":
        j = i + 2
        while j < n and (src[j] in "0123456789abcdefABCDEF._"):
            j += 1
        if j < n and src[j] in "pP":
            j += 1
            if j < n and src[j] in "+-":
                j += 1
            while j < n and (src[j].isdigit() or src[j] == "_"):
                j += 1
        return j

    if c == "0" and i + 1 < n and src[i + 1] in "bB":
        j = i + 2
        while j < n and (src[j] in "01_"):
            j += 1
        return j

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


def _need_space(left, right):
    if left.type == T_EOF or right.type == T_EOF:
        return False

    lt, rt = left.type, right.type
    lv, rv = left.value, right.value

    if lt == T_IDENT and rt == T_IDENT:
        return True

    if lt == T_IDENT and rt == T_NUMBER:
        return True

    if lt == T_NUMBER and rt == T_IDENT:
        return True

    if lt == T_NUMBER and rt == T_NUMBER:
        return True

    if lt == T_NUMBER and rt == T_OP and rv[0] == ".":
        return True

    if lt == T_OP and lv == "-" and rt == T_OP and rv == "-":
        return True

    if lt == T_OP and lv == "[" and rt == T_OP and rv and rv[0] == "[":
        return True

    if lt == T_OP and lv == ".." and rt == T_OP and rv == ".":
        return True

    if lt == T_OP and lv == "=" and rt == T_OP and rv == "=":
        return True

    if lt == T_OP and lv == "<" and rt == T_OP and rv == "<":
        return True

    if lt == T_OP and lv == ">" and rt == T_OP and rv == ">":
        return True

    if lt == T_OP and lv == ">" and rt == T_OP and rv == ">=":
        return True

    if lt == T_OP and lv == "/" and rt == T_OP and rv == "/":
        return True

    if lt == T_OP and lv == "/" and rt == T_OP and rv == "//=":
        return True

    if lt == T_OP and lv == "~" and rt == T_OP and rv == "=":
        return True

    if lt == T_OP and lv == ":" and rt == T_OP and rv == ":":
        return True

    if lt == T_OP and lv == "-" and rt == T_OP and rv == ">":
        return True

    return False


def _need_semicolon(left, right):
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
    if "\\z" not in value:
        return value

    if not value or value[0] not in "\"'":
        return value
    quote = value[0]
    out = [quote]
    i = 1
    n = len(value)
    while i < n - 1:
        c = value[i]
        if c == "\\" and i + 1 < n:
            nxt = value[i + 1]
            if nxt == "z":
                i += 2
                while i < n - 1 and value[i] in " \t\r\n\f\v":
                    i += 1
                continue
            else:
                out.append(value[i:i + 2])
                i += 2
                continue
        out.append(c)
        i += 1
    out.append(quote)
    return "".join(out)


def _long_string_to_short(value):
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

    content = value[len(open_bracket):-len(close_bracket)]

    if content.startswith("\n"):
        content = content[1:]
    elif content.startswith("\r\n"):
        content = content[2:]
    elif content.startswith("\r"):
        content = content[1:]

    has_double = '"' in content
    has_single = "'" in content
    if not has_double:
        quote = '"'
        escaped = content.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace('"', '\\"')
    elif not has_single:
        quote = "'"
        escaped = content.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("'", "\\'")
    else:
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
    tokens = tokenize(src)

    stripped = [t for t in tokens if t.type != T_COMMENT]

    out = []
    table_depth = 0
    paren_depth = 0

    for k in range(len(stripped)):
        cur = stripped[k]
        if cur.type == T_EOF:
            break

        if cur.type == T_OP and cur.value == ";":
            if table_depth > 0:
                pass
            else:
                nxt = stripped[k + 1] if k + 1 < len(stripped) else Token(T_EOF, "")
                if not _need_semicolon(cur, nxt):
                    continue

        if convert_long_strings and cur.type == T_STRING and cur.value.startswith("["):
            converted = _long_string_to_short(cur.value)
            if converted is not None:
                cur = Token(T_STRING, converted)

        if cur.type == T_STRING:
            cur = Token(T_STRING, _normalize_short_string(cur.value))

        if out:
            prev_token = _last_real_token(stripped, k)
            if prev_token is not None and _need_space(prev_token, cur):
                out.append(" ")

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
    j = k - 1
    while j >= 0:
        t = stripped[j]
        if t.type == T_OP and t.value == ";":
            nxt = stripped[j + 1] if j + 1 < len(stripped) else Token(T_EOF, "")
            if _need_semicolon(t, nxt):
                return t
            j -= 1
            continue
        return t
    return None


def _strip_meaningful(tokens, normalize_long_strings=False, normalize_z_escape=False,
                       ignore_semicolons=True):
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
            f"token count differs: orig={len(orig_tokens)}, mini={len(mini_tokens)}"
        )

    for i, (a, b) in enumerate(zip(orig_tokens, mini_tokens)):
        if a != b:
            return False, (
                f"token #{i} differs:\n"
                f"  orig: {a}\n"
                f"  mini: {b}"
            )

    return True, None


def main():
    parser = argparse.ArgumentParser(
        description="Roblox Luau minifier.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python minifier.py script.lua\n"
            "  python minifier.py script.lua -o min.lua\n"
            "  python minifier.py script.lua --check\n"
            "  python minifier.py script.lua --check --luau /path/to/luau-compile\n"
        ),
    )
    parser.add_argument("input", help="path to .lua/.luau file")
    parser.add_argument("-o", "--output", help="output file (default: stdout)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify output with luau-compile + token equivalence",
    )
    parser.add_argument(
        "--luau",
        default=_detect_luau_compile(),
        help="path to luau-compile (for --check)",
    )
    parser.add_argument(
        "--no-token-check",
        action="store_true",
        help="skip token equivalence check",
    )
    args = parser.parse_args()

    src_path = Path(args.input)
    if not src_path.is_file():
        print(f"error: file not found — {args.input}", file=sys.stderr)
        sys.exit(2)

    src = src_path.read_text(encoding="utf-8")

    try:
        minified = minify(src)
    except Exception as e:
        print(f"error during minify: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        Path(args.output).write_text(minified, encoding="utf-8")
    else:
        sys.stdout.write(minified)

    if args.check:
        ok = True

        if not args.no_token_check:
            equiv, msg = check_token_equivalence(src, minified)
            if equiv:
                print("[OK] token equivalence", file=sys.stderr)
            else:
                ok = False
                print(f"[FAIL] token equivalence broken:\n{msg}", file=sys.stderr)

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
                    print("[OK] luau-compile: syntax ok", file=sys.stderr)
                else:
                    ok = False
                    print(f"[FAIL] luau-compile error:\n{proc.stderr.strip()}",
                          file=sys.stderr)
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        else:
            print("[WARN] luau-compile not found — skipping syntax check. "
                  "Pass --luau /path/to/luau-compile", file=sys.stderr)

        orig_size = len(src.encode("utf-8"))
        mini_size = len(minified.encode("utf-8"))
        ratio = (mini_size / orig_size * 100) if orig_size else 0
        print(f"[STAT] size: {orig_size} -> {mini_size} bytes ({ratio:.1f}%)",
              file=sys.stderr)

        sys.exit(0 if ok else 1)


def _detect_luau_compile():
    import shutil
    return shutil.which("luau-compile")


if __name__ == "__main__":
    main()
