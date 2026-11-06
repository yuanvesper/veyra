#!/usr/bin/env bash
# Veyra install script (Bash) - install / update / uninstall the mod-issue-triage Skill.
# Safety: absolute destination only, path-component escape rejection, pre-normalization
# symlink checks, Python-based ancestor/descendant overlap detection, ownership marker
# validation, atomic lock file, exclusive temp/backup/trash creation, internal-link
# scanning, captured find exit codes (temporary NUL-separated lists), and transactional
# replace with rollback.
#
# Note: these checks mitigate TOCTOU races; they are not a cryptographic security boundary.
#
# Usage:
#   ./scripts/install.sh --dest /absolute/path/to/skills
#   ./scripts/install.sh --dest /absolute/path/to/skills --update
#   ./scripts/install.sh --dest /absolute/path/to/skills --uninstall

set -u

SKILL_NAME="mod-issue-triage"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SOURCE_SKILL="$REPO_ROOT/skills/$SKILL_NAME"
REQUIRED_FILES=("SKILL.md" "references/report-template.md" "evals/README.md")

DEST=""
MODE="install"
HAS_LOCK=0
LOCK_PATH=""
UPDATE_FLAG=0
UNINSTALL_FLAG=0
TEMP_FILES=()
OWNED_PATHS=()
LAST_LIST=""

die() {
    echo "[veyra][错误] $*" >&2
    exit 1
}

warn() {
    echo "[veyra][警告] $*" >&2
}

step() {
    echo "[veyra] $*"
}

cleanup_temp_files() {
    local file
    if [ "${#TEMP_FILES[@]}" -gt 0 ]; then
        for file in "${TEMP_FILES[@]}"; do
            if [ -f "$file" ]; then
                rm -f -- "$file" || warn "临时清单清理失败：$file"
            fi
        done
    fi
}

register_owned() {
    OWNED_PATHS+=("$1")
}

unregister_owned() {
    local target="$1"
    local kept=()
    local path
    for path in "${OWNED_PATHS[@]}"; do
        [ "$path" = "$target" ] || kept+=("$path")
    done
    OWNED_PATHS=("${kept[@]}")
}

cleanup_owned() {
    local path
    for path in "${OWNED_PATHS[@]}"; do
        [ -n "$path" ] || continue
        if [ -e "$path" ] || [ -L "$path" ]; then
            remove_tree_no_follow "$path" 2>/dev/null || warn "本次资源清理失败（保留路径）：$path"
        fi
    done
    OWNED_PATHS=()
}

cleanup_lock() {
    if [ "$HAS_LOCK" = "1" ]; then
        rm -f -- "$LOCK_PATH" 2>/dev/null || warn "锁文件清理失败：$LOCK_PATH"
        HAS_LOCK=0
    fi
}

cleanup_hash_registry() {
    [ -n "$HASH_TEMP_REGISTRY" ] || return 0
    [ -f "$HASH_TEMP_REGISTRY" ] || return 0
    local f
    while IFS= read -r f; do
        [ -n "$f" ] && rm -f -- "$f" 2>/dev/null
    done < "$HASH_TEMP_REGISTRY"
    rm -f -- "$HASH_TEMP_REGISTRY" 2>/dev/null
}

on_exit() {
    cleanup_owned
    cleanup_hash_registry
    cleanup_lock
    cleanup_temp_files
}

trap on_exit EXIT

usage() {
    cat <<'EOF'
Usage: install.sh --dest <absolute-dir> [--update | --uninstall]
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dest)
            [ $# -ge 2 ] || die "--dest 缺少参数"
            DEST="$2"
            shift 2
            ;;
        --update)
            UPDATE_FLAG=1
            MODE="update"
            shift
            ;;
        --uninstall)
            UNINSTALL_FLAG=1
            MODE="uninstall"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "未知参数：$1"
            ;;
    esac
done

if [ "$UPDATE_FLAG" = "1" ] && [ "$UNINSTALL_FLAG" = "1" ]; then
    die "参数冲突：--update 与 --uninstall 不能同时使用"
fi

# --- Path safety -----------------------------------------------------------

check_raw_components() {
    local raw="$1"
    local -a parts
    local part
    IFS='/' read -r -a parts <<< "$raw"
    for part in "${parts[@]}"; do
        [ -z "$part" ] && continue
        [ "$part" = "." ] && continue
        [ "$part" = ".." ] && die "路径包含 ..，已拒绝"
    done
}

# Check every existing prefix of the original (un-normalized) path for symlinks.
check_raw_prefixes_no_links() {
    local raw="$1"
    local current="/"
    local -a parts
    local part
    IFS='/' read -r -a parts <<< "$raw"
    for part in "${parts[@]}"; do
        [ -z "$part" ] && continue
        [ "$part" = "." ] && continue
        if [ "$current" = "/" ]; then
            current="/$part"
        else
            current="$current/$part"
        fi
        if [ -L "$current" ]; then
            die "发现符号链接或重解析点：$current"
        fi
    done
}

assert_no_symlink_chain() {
    local current="$1"
    while [ -n "$current" ] && [ "$current" != "/" ]; do
        if [ -L "$current" ]; then
            die "发现符号链接或重解析点：$current"
        fi
        current="$(dirname "$current")"
    done
}

# Python-based normalization + bidirectional overlap detection (commonpath).
# Exit codes: 10 = equal, 11 = dest inside protected, 12 = dest is ancestor of protected.
resolve_and_check_overlap() {
    local raw="$1"
    "$PYTHON_BIN" -c 'import os, sys
dest = os.path.abspath(sys.argv[1])
repo = os.path.abspath(sys.argv[2])
source = os.path.abspath(sys.argv[3])
def relation(a, b):
    if a == b:
        return "equal"
    try:
        common = os.path.commonpath([a, b])
    except ValueError:
        return "none"
    if common == a:
        return "ancestor"
    if common == b:
        return "descendant"
    return "none"
for other, code_equal, code_inside, code_ancestor in ((repo, 10, 11, 12), (source, 10, 11, 12)):
    rel = relation(dest, other)
    if rel == "equal":
        sys.exit(code_equal)
    if rel == "descendant":
        sys.exit(code_inside)
    if rel == "ancestor":
        sys.exit(code_ancestor)
print(dest)' "$raw" "$REPO_ROOT" "$SOURCE_SKILL"
}

resolve_destination() {
    [ -n "$DEST" ] || die "Destination 不能为空"
    case "$DEST" in
        /*) : ;;
        *) die "Destination 必须是绝对路径" ;;
    esac
    check_raw_components "$DEST"
    check_raw_prefixes_no_links "$DEST"

    local normalized
    normalized="$(resolve_and_check_overlap "$DEST")"
    local rc=$?
    case "$rc" in
        0) : ;;
        10) die "Destination 不能等于仓库根目录或正式 Skill 源目录：$DEST" ;;
        11) die "Destination 不能位于仓库根目录或正式 Skill 源目录内部：$DEST" ;;
        12) die "Destination 不能是仓库根目录或正式 Skill 源目录的祖先目录：$DEST" ;;
        *) die "无法安全解析 Destination：$DEST（错误码 $rc）" ;;
    esac
    DEST="$normalized"
    [ "$DEST" = "/" ] && die "拒绝敏感目录：$DEST"

    if [ -e "$DEST" ]; then
        [ -d "$DEST" ] || die "Destination 已存在但不是目录：$DEST"
    else
        local probe="$DEST"
        while [ ! -e "$probe" ]; do
            probe="$(dirname "$probe")"
        done
        assert_no_symlink_chain "$probe"
        mkdir -p -- "$DEST" || die "无法创建 Destination：$DEST"
    fi

    assert_no_symlink_chain "$DEST"
}

acquire_lock() {
    LOCK_PATH="$DEST/.veyra-operation.lock"
    if [ -e "$LOCK_PATH" ] || [ -L "$LOCK_PATH" ]; then
        die "发现锁文件 $LOCK_PATH。可能另一个操作正在运行或上次异常退出；请人工检查后删除，脚本不会自动清理。"
    fi
    set -o noclobber
    if ! ( : > "$LOCK_PATH" ) 2>/dev/null; then
        set +o noclobber
        die "无法获取锁文件（可能并发冲突）：$LOCK_PATH"
    fi
    set +o noclobber
    HAS_LOCK=1
}

# --- Skill tree helpers ----------------------------------------------------

unique_id() {
    "$PYTHON_BIN" -c 'import uuid; print(uuid.uuid4().hex)'
}

new_exclusive_dir() {
    local path="$1"
    if ! mkdir -- "$path" 2>/dev/null; then
        die "无法独占创建目录（目标已存在或不可创建）：$path"
    fi
}

# Create a temporary NUL-separated file list. The find exit code is checked before
# the list is ever read; a failure aborts the operation instead of using partial data.
make_file_list() {
    local dir="$1"
    shift
    LAST_LIST="$(mktemp "${TMPDIR:-/tmp}/veyra-list.XXXXXX")" || {
        echo "无法创建临时清单文件" >&2
        return 1
    }
    TEMP_FILES+=("$LAST_LIST")
    find "$dir" "$@" -print0 > "$LAST_LIST"
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "扫描失败（find 退出码 $rc）：$dir" >&2
        return 1
    fi
    return 0
}

scan_no_links() {
    local dir="$1"
    local item
    make_file_list "$dir" || return 1
    while IFS= read -r -d '' item; do
        [ "$item" = "$dir" ] && continue
        if [ -L "$item" ]; then
            echo "发现符号链接或重解析点，拒绝操作：$item" >&2
            return 1
        fi
    done < "$LAST_LIST"
    return 0
}

remove_tree_no_follow() {
    local dir="$1"
    local item
    make_file_list "$dir" -depth || return 1
    while IFS= read -r -d '' item; do
        [ "$item" = "$dir" ] && continue
        if [ -L "$item" ]; then
            echo "发现符号链接或重解析点，拒绝继续删除：$item" >&2
            return 1
        fi
    done < "$LAST_LIST"
    while IFS= read -r -d '' item; do
        [ "$item" = "$dir" ] && continue
        if [ -f "$item" ]; then
            rm -f -- "$item" || return 1
        elif [ -d "$item" ]; then
            rmdir -- "$item" || return 1
        fi
    done < "$LAST_LIST"
    rmdir -- "$dir" || return 1
}

copy_tree() {
    local src="$1"
    local dst="$2"
    local item rel
    make_file_list "$src" || return 1
    while IFS= read -r -d '' item; do
        [ "$item" = "$src" ] && continue
        if [ -L "$item" ]; then
            echo "发现符号链接或重解析点，拒绝操作：$item" >&2
            return 1
        fi
        rel="${item#"$src"/}"
        if [ -d "$item" ]; then
            mkdir -p -- "$dst/$rel" || return 1
        else
            mkdir -p -- "$(dirname "$dst/$rel")" || return 1
            cp -p -- "$item" "$dst/$rel" || return 1
        fi
    done < "$LAST_LIST"

    make_file_list "$src" -type f || return 1
    while IFS= read -r -d '' item; do
        [ "$item" = "$src" ] && continue
        rel="${item#"$src"/}"
        [ -f "$dst/$rel" ] || { echo "复制校验失败，缺少文件：$rel" >&2; return 1; }
        local src_size dst_size
        src_size="$(wc -c < "$item")"
        dst_size="$(wc -c < "$dst/$rel")"
        [ "$src_size" = "$dst_size" ] || { echo "复制校验失败，大小不一致：$rel" >&2; return 1; }
    done < "$LAST_LIST"

    local required
    for required in "${REQUIRED_FILES[@]}"; do
        [ -f "$dst/$required" ] || { echo "完整性校验失败，缺少 $required" >&2; return 1; }
    done
}

validate_marker() {
    local marker="$1"
    [ -f "$marker" ] || die "缺少 .veyra-install.json，拒绝操作"
    if [ -L "$marker" ]; then
        die "发现符号链接或重解析点：$marker"
    fi
    "$PYTHON_BIN" -c 'import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    sys.exit(10)
owner = data.get("owner")
name = data.get("skill_name")
version = data.get("schema_version")
if not isinstance(owner, str) or owner != "veyra":
    sys.exit(11)
if not isinstance(name, str) or name != "mod-issue-triage":
    sys.exit(12)
if not (isinstance(version, int) and not isinstance(version, bool) and version == 1):
    sys.exit(13)
sys.exit(0)' "$marker" || die "标记文件无效（JSON、owner、skill_name 或 schema_version 校验失败）"
}

write_marker() {
    local target="$1"
    printf '%s' '{"owner":"veyra","skill_name":"mod-issue-triage","schema_version":1}' > "$target/.veyra-install.json"
}

assert_operation_targets_safe() {
    local dest_full="$1"
    local skill_dir="$2"
    shift 2
    assert_no_symlink_chain "$dest_full"
    assert_no_symlink_chain "$skill_dir"
    local extra
    for extra in "$@"; do
        [ -L "$extra" ] && die "发现符号链接或重解析点：$extra"
    done
}

soft_no_symlink_chain() {
    local current="$1"
    while [ -n "$current" ] && [ "$current" != "/" ]; do
        if [ -L "$current" ]; then
            echo "发现符号链接或重解析点：$current" >&2
            return 1
        fi
        current="$(dirname "$current")"
    done
    return 0
}

soft_operation_targets_safe() {
    local dest_full="$1"
    local skill_dir="$2"
    shift 2
    soft_no_symlink_chain "$dest_full" || return 1
    soft_no_symlink_chain "$skill_dir" || return 1
    local extra
    for extra in "$@"; do
        if [ -L "$extra" ]; then
            echo "发现符号链接或重解析点：$extra" >&2
            return 1
        fi
    done
    return 0
}

# --- Operations ------------------------------------------------------------

do_install() {
    local skill="$DEST/$SKILL_NAME"
    if [ -e "$skill" ] || [ -L "$skill" ]; then
        die "目标已存在：$skill。如要更新请使用 --update"
    fi
    local tmp
    tmp="$DEST/.veyra-tmp-$(unique_id)"
    new_exclusive_dir "$tmp"
    register_owned "$tmp"
    if ! copy_tree "$SOURCE_SKILL" "$tmp"; then
        die "复制 Skill 文件失败"
    fi
    write_marker "$tmp" || die "写入标记文件失败"
    if ! soft_operation_targets_safe "$DEST" "$skill" "$tmp"; then
        die "安全检查失败，拒绝安装"
    fi
    if [ -e "$skill" ] || [ -L "$skill" ]; then
        die "目标在安装过程中出现，拒绝覆盖"
    fi
    mv -- "$tmp" "$skill" || die "安装失败"
    if ! assert_skill_complete "$skill"; then
        if remove_tree_no_follow "$skill" 2>/dev/null; then
            die "安装后完整性校验失败，已清理半成品：$skill"
        fi
        warn "安装后完整性校验失败，半成品清理失败，保留路径：$skill"
        die "安装未完成：请人工检查 $skill"
    fi
    unregister_owned "$tmp"
    step "安装成功：$skill"
}

HASH_TOOL=""
HASH_TEMP_REGISTRY=""

detect_hash_command() {
    if command -v sha256sum >/dev/null 2>&1; then
        HASH_TOOL="sha256sum"
        return 0
    fi
    if command -v shasum >/dev/null 2>&1; then
        HASH_TOOL="shasum"
        return 0
    fi
    HASH_TOOL=""
HASH_TEMP_REGISTRY=""
    return 1
}

hash_file() {
    local file="$1"
    local tmp output rc digest rest lines
    [ -n "$HASH_TOOL" ] || return 1
    [ -f "$file" ] || return 1
    tmp="$(mktemp "${TMPDIR:-/tmp}/veyra-hash.XXXXXX")" || return 1
    TEMP_FILES+=("$tmp")
    # 命令替换会在子 shell 中执行本函数，数组改动不会传回父 shell；
    # 因此额外写入共享登记文件，供 EXIT trap 兜底清理。
    if [ -n "$HASH_TEMP_REGISTRY" ]; then
        printf '%s\n' "$tmp" >> "$HASH_TEMP_REGISTRY" 2>/dev/null || true
    fi
    case "$HASH_TOOL" in
        sha256sum)
            sha256sum "$file" > "$tmp" 2>/dev/null
            rc=$?
            ;;
        shasum)
            shasum -a 256 "$file" > "$tmp" 2>/dev/null
            rc=$?
            ;;
        *)
            rm -f -- "$tmp"
            return 1
            ;;
    esac
    if [ "$rc" -ne 0 ]; then
        rm -f -- "$tmp"
        return 1
    fi
    lines="$(wc -l < "$tmp" | tr -d '[:space:]')"
    if [ "$lines" != "1" ]; then
        rm -f -- "$tmp"
        return 1
    fi
    output="$(cat "$tmp")"
    rm -f -- "$tmp"
    [ -n "$output" ] || return 1
    [ "${#output}" -ge 66 ] || return 1
    digest="${output:0:64}"
    rest="${output:64}"
    case "$digest" in
        *[!0-9a-fA-F]*) return 1 ;;
    esac
    if ! [[ "$rest" =~ ^[[:space:]]+[^[:space:]] ]]; then
        return 1
    fi
    printf '%s\n' "$digest" | tr 'A-F' 'a-f'
}
assert_skill_complete() {
    local dir="$1"
    local required
    [ -d "$dir" ] || { echo "完整性校验失败：目录不存在：$dir" >&2; return 1; }
    for required in "${REQUIRED_FILES[@]}"; do
        [ -f "$dir/$required" ] || { echo "完整性校验失败，缺少 $required：$dir" >&2; return 1; }
    done
    return 0
}
restore_original() {
    local skill="$1"
    local backup="$2"
    local backed="$2/$SKILL_NAME"
    local expected actual restore_rc returned_hash

    if [ -e "$skill" ] || [ -L "$skill" ]; then
        echo "回滚目标已存在，拒绝覆盖或合并：$skill（备份保留在：$backup）" >&2
        return 1
    fi
    if [ -L "$backed" ] || [ ! -d "$backed" ]; then
        echo "回滚前校验失败：备份根节点不是普通目录：$backed（备份保留在：$backup）" >&2
        return 1
    fi
    if ! soft_no_symlink_chain "$DEST"; then
        echo "回滚前校验失败：Destination 路径链存在符号链接（备份保留在：$backup）" >&2
        return 1
    fi
    if ! soft_no_symlink_chain "$backup"; then
        echo "回滚前校验失败：备份路径链存在符号链接（备份保留在：$backup）" >&2
        return 1
    fi
    if ! soft_no_symlink_chain "$backed"; then
        echo "回滚前校验失败：备份 Skill 路径链存在符号链接（备份保留在：$backup）" >&2
        return 1
    fi
    if ! scan_no_links "$backed"; then
        echo "回滚前校验失败：备份内部存在符号链接（备份保留在：$backup）" >&2
        return 1
    fi
    if [ -L "$backed/SKILL.md" ] || [ ! -f "$backed/SKILL.md" ]; then
        echo "回滚前校验失败：备份缺少普通 SKILL.md（备份保留在：$backup）" >&2
        return 1
    fi
    if ! expected="$(hash_file "$backed/SKILL.md")"; then
        echo "回滚前校验失败：无法计算备份 SKILL.md 的 SHA256（备份保留在：$backup）" >&2
        return 1
    fi
    mv -- "$backed" "$skill" || { echo "回滚移动失败（备份保留在：$backup）" >&2; return 1; }

    if [ -L "$skill" ] || [ ! -d "$skill" ] || [ -L "$skill/SKILL.md" ] || [ ! -f "$skill/SKILL.md" ]; then
        echo "回滚后校验失败：正式位置未正确恢复：$skill" >&2
        restore_rc=1
    else
        if ! actual="$(hash_file "$skill/SKILL.md")"; then
            echo "回滚后校验失败：无法计算正式位置 SKILL.md 的 SHA256：$skill" >&2
            restore_rc=1
        elif [ "$actual" != "$expected" ]; then
            echo "回滚后校验失败：SKILL.md SHA256 不一致（期望 $expected，实际 $actual）：$skill" >&2
            restore_rc=1
        elif ! assert_skill_complete "$skill"; then
            echo "回滚后校验失败：目录内容不完整：$skill" >&2
            restore_rc=1
        else
            restore_rc=0
        fi
    fi

    if [ "$restore_rc" = "0" ]; then
        remove_tree_no_follow "$backup" 2>/dev/null || warn "空备份目录清理失败：$backup"
        return 0
    fi

    # 回滚后校验失败：尝试把可能包含原始版本的正式目录安全地退回备份位置。
    if [ -e "$backup/$SKILL_NAME" ] || [ -L "$backup/$SKILL_NAME" ]; then
        echo "回滚后校验失败，且备份位置已被占用；原始版本位置无法确认：$skill 或 $backup" >&2
        return 2
    fi
    if ! soft_no_symlink_chain "$backup"; then
        echo "回滚后校验失败，且备份路径不安全；原始版本位置无法确认：$skill 或 $backup" >&2
        return 2
    fi
    # 反向移动前重新扫描正式位置（经过回滚与校验后状态可能已变）。
    if ! soft_no_symlink_chain "$skill"; then
        echo "正式位置路径链不安全，原始版本位置无法确认：$skill 或 $backup" >&2
        return 2
    fi
    if ! scan_no_links "$skill"; then
        echo "正式位置内部存在符号链接或扫描失败，原始版本位置无法确认：$skill 或 $backup" >&2
        return 2
    fi
    if ! mv -- "$skill" "$backup"; then
        echo "回滚后校验失败，且无法退回备份；原始版本位置无法确认：$skill 或 $backup" >&2
        return 2
    fi
    # 反向移动后、校验 SHA256 前，重新扫描备份路径链与内部链接。
    if ! soft_no_symlink_chain "$backup/$SKILL_NAME"; then
        echo "退回后备份路径链不安全，原始版本位置无法确认：$backup/$SKILL_NAME" >&2
        return 2
    fi
    if ! scan_no_links "$backup/$SKILL_NAME"; then
        echo "退回后备份内部存在符号链接或扫描失败，原始版本位置无法确认：$backup/$SKILL_NAME" >&2
        return 2
    fi
    if [ -L "$backup/$SKILL_NAME" ] || [ ! -d "$backup/$SKILL_NAME" ] || [ -L "$backup/$SKILL_NAME/SKILL.md" ] || [ ! -f "$backup/$SKILL_NAME/SKILL.md" ]; then
        echo "回滚后校验失败，退回后备份结构异常；原始版本位置无法确认：$backup/$SKILL_NAME" >&2
        return 2
    fi
    if ! returned_hash="$(hash_file "$backup/$SKILL_NAME/SKILL.md")"; then
        echo "回滚后校验失败，退回后无法计算 SHA256；原始版本位置无法确认：$backup/$SKILL_NAME" >&2
        return 2
    fi
    if [ "$returned_hash" = "$expected" ]; then
        if ! assert_skill_complete "$backup/$SKILL_NAME"; then
            echo "回滚后校验失败，退回备份后目录内容不完整；不能确认原始版本完整恢复：$backup/$SKILL_NAME" >&2
            return 2
        fi
        echo "回滚后校验失败，原始版本已退回备份：$backup（SHA256=$returned_hash）" >&2
        return 1
    fi
    echo "回滚后校验失败，已退回备份但内容已损坏（期望 $expected，实际 $returned_hash）；不能确认原始版本完整恢复：$backup/$SKILL_NAME" >&2
    return 2
}
do_update() {
    local skill="$DEST/$SKILL_NAME"
    [ -d "$skill" ] || die "未找到已安装的 Skill：$skill"
    assert_no_symlink_chain "$skill"
    scan_no_links "$skill" || die "扫描失败或发现符号链接，拒绝操作：$skill"
    validate_marker "$skill/.veyra-install.json"
    if ! detect_hash_command; then
        die "未找到可用的 SHA256 工具（sha256sum 或 shasum）；为避免在未校验的情况下移动原始版本，已拒绝更新"
    fi

    local id tmp backup
    id="$(unique_id)"
    tmp="$DEST/.veyra-tmp-$id"
    backup="$DEST/.veyra-bak-$id"

    new_exclusive_dir "$tmp"
    register_owned "$tmp"
    if ! copy_tree "$SOURCE_SKILL" "$tmp"; then
        die "复制 Skill 文件失败"
    fi
    write_marker "$tmp" || die "写入标记文件失败"

    new_exclusive_dir "$backup"
    register_owned "$backup"
    if ! soft_operation_targets_safe "$DEST" "$skill" "$tmp" "$backup"; then
        die "安全检查失败，拒绝更新"
    fi
    scan_no_links "$skill" || die "扫描失败或发现符号链接，拒绝更新：$skill"

    mv -- "$skill" "$backup" || die "无法移动原版本到备份目录"
    # 备份此时包含原始版本：从自动清理登记中移除，只能由显式事务逻辑处理。
    unregister_owned "$backup"

    if ! soft_operation_targets_safe "$DEST" "$skill" "$tmp"; then
        restore_original "$skill" "$backup"
        restore_rc=$?
        if [ "$restore_rc" -eq 0 ]; then
            die "安全复检失败，已回滚到原版本"
        fi
        if [ "$restore_rc" -eq 2 ]; then
            die "安全复检失败且回滚失败，原始版本位置无法确认（请检查 $skill 或 $backup）"
        fi
        die "安全复检失败且回滚失败，备份保留在：$backup"
    fi

    if ! mv -- "$tmp" "$skill"; then
        restore_original "$skill" "$backup"
        restore_rc=$?
        if [ "$restore_rc" -eq 0 ]; then
            die "替换失败，已回滚到原版本"
        fi
        if [ "$restore_rc" -eq 2 ]; then
            die "替换失败且回滚失败，原始版本位置无法确认（请检查 $skill 或 $backup）"
        fi
        die "替换失败且回滚失败，备份保留在：$backup"
    fi
    if ! assert_skill_complete "$skill"; then
        local broken="$DEST/.veyra-broken-$id"
        if [ -e "$broken" ] || [ -L "$broken" ]; then
            die "新版本完整性校验失败，且隔离目录名冲突：$broken；原版本备份保留在：$backup"
        fi
        if ! mkdir -- "$broken" 2>/dev/null; then
            die "新版本完整性校验失败，且无法创建隔离目录：$broken；原版本备份保留在：$backup"
        fi
        register_owned "$broken"
        if ! mv -- "$skill" "$broken"; then
            die "新版本完整性校验失败，且无法移走半成品：$skill；原版本备份保留在：$backup"
        fi
        if restore_original "$skill" "$backup"; then
            remove_tree_no_follow "$broken" 2>/dev/null || warn "半成品清理失败，保留路径：$broken"
            unregister_owned "$broken"
            die "新版本完整性校验失败，已回滚到原版本"
        fi
        unregister_owned "$broken"
        die "新版本完整性校验失败，半成品保留在：$broken；原版本备份保留在：$backup"
    fi
    unregister_owned "$tmp"

    if remove_tree_no_follow "$backup"; then
        step "更新成功：$skill"
    else
        warn "备份清理失败（新版本已就位）：$backup"
        step "更新成功（存在残留备份）：$skill"
    fi
}

do_uninstall() {
    local skill="$DEST/$SKILL_NAME"
    [ -d "$skill" ] || die "未找到已安装的 Skill：$skill"
    assert_no_symlink_chain "$skill"
    scan_no_links "$skill" || die "扫描失败或发现符号链接，拒绝操作：$skill"
    validate_marker "$skill/.veyra-install.json"

    local trash
    trash="$DEST/.veyra-trash-$(unique_id)"
    new_exclusive_dir "$trash"
    register_owned "$trash"
    if ! soft_operation_targets_safe "$DEST" "$skill" "$trash"; then
        die "安全检查失败，拒绝卸载"
    fi
    scan_no_links "$skill" || die "扫描失败或发现符号链接，拒绝卸载：$skill"
    mv -- "$skill" "$trash" || die "无法将目标移动到隔离目录"
    # 隔离目录此时包含内容：从自动清理登记中移除，失败时保留并报告路径。
    unregister_owned "$trash"

    if remove_tree_no_follow "$trash"; then
        step "卸载成功：$skill"
    else
        warn "隔离目录清理失败，保留路径：$trash（内容可能不完整）"
        die "卸载未完全完成：请人工检查 $trash"
    fi
}

PYTHON_BIN="$(command -v python 2>/dev/null || command -v python3 2>/dev/null || true)"
[ -n "$PYTHON_BIN" ] || die "找不到 python 或 python3，无法校验标记文件"

HASH_TEMP_REGISTRY="$(mktemp "${TMPDIR:-/tmp}/veyra-hashreg.XXXXXX" 2>/dev/null || true)"
if [ -n "$HASH_TEMP_REGISTRY" ]; then
    TEMP_FILES+=("$HASH_TEMP_REGISTRY")
fi

resolve_destination
assert_no_symlink_chain "$REPO_ROOT"
assert_no_symlink_chain "$SOURCE_SKILL"
acquire_lock

case "$MODE" in
    install) do_install ;;
    update) do_update ;;
    uninstall) do_uninstall ;;
esac
