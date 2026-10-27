#!/bin/sh
# Bootstrap uses OS tools only; the engine carries Python and all Python dependencies.
set -eu
skill_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -n "${PHOTOBOOK_ENGINE:-}" ]; then exec "$PHOTOBOOK_ENGINE" "$@"; fi
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) platform=macos-arm64 ;;
  Darwin-x86_64) platform=macos-x64 ;;
  Linux-x86_64) platform=linux-x64 ;;
  *) printf '%s\n' '尚未发布此系统/架构的运行包。' >&2; exit 2 ;;
esac
version=0.2.2
if [ -n "${PHOTOBOOK_CACHE:-}" ]; then cache=$PHOTOBOOK_CACHE
elif [ "$(uname -s)" = Darwin ]; then cache="$HOME/Library/Caches/travel-photobook"
else cache="${XDG_CACHE_HOME:-$HOME/.cache}/travel-photobook"; fi
manifest="$skill_dir/assets/runtime/$platform"
if [ ! -f "$manifest.url" ] || [ ! -f "$manifest.sha256" ]; then
  printf '%s\n' '此源码包尚未配置已发布的运行包；不能把技能说明已下载当作安装完成。开发者需先构建运行包并生成发行清单。' >&2; exit 2
fi
url=$(cat "$manifest.url"); expected=$(cat "$manifest.sha256")
case "$url" in
  https://*) ;;
  file://*) [ "${PHOTOBOOK_ALLOW_LOCAL_RELEASE:-0}" = 1 ] || { printf '%s\n' '本地发行仅用于开发验证。' >&2; exit 2; } ;;
  *) printf '%s\n' '运行包下载地址不合法。' >&2; exit 2 ;;
esac
case "$expected" in *[!0-9a-f]*|'') exit 2 ;; esac
[ "${#expected}" -eq 64 ] || exit 2
target="$cache/runtime/$version/$platform-$expected"
engine="$target/photobook-engine/photobook-engine"
if [ ! -x "$engine" ] || [ ! -f "$target/.ready" ]; then
  mkdir -p "$cache/runtime/$version"
  install_lock="$cache/runtime/$version/.lock-$platform-$expected"
  attempts=0
  until mkdir "$install_lock" 2>/dev/null; do
    if [ -f "$install_lock/pid" ]; then
      owner=$(cat "$install_lock/pid")
      case "$owner" in *[!0-9]*|'') ;; *)
        if ! kill -0 "$owner" 2>/dev/null; then rm -f "$install_lock/pid"; rmdir "$install_lock" 2>/dev/null || true; continue; fi ;;
      esac
    fi
    if [ -x "$engine" ] && [ -f "$target/.ready" ]; then exec "$engine" "$@"; fi
    attempts=$((attempts+1))
    [ "$attempts" -le 30 ] || { printf '%s\n' '另一任务正在准备运行组件，助手应稍后重试。' >&2; exit 2; }
    sleep 1
  done
  printf '%s\n' "$$" > "$install_lock/pid"
  staging=$(mktemp -d "$cache/runtime/$version/.install-XXXXXX")
  trap 'rm -rf "$staging"; rm -f "$install_lock/pid"; rmdir "$install_lock" 2>/dev/null || true' EXIT HUP INT TERM
  curl --fail --location --retry 3 --connect-timeout 20 "$url" -o "$staging/runtime.zip"
  if command -v sha256sum >/dev/null 2>&1; then actual=$(sha256sum "$staging/runtime.zip" | cut -d ' ' -f 1)
  elif command -v openssl >/dev/null 2>&1; then actual=$(openssl dgst -sha256 -r "$staging/runtime.zip" | cut -d ' ' -f 1)
  elif command -v shasum >/dev/null 2>&1; then actual=$(LC_ALL=C LANG=C shasum -a 256 "$staging/runtime.zip" | cut -d ' ' -f 1)
  else printf '%s\n' '系统缺少SHA-256校验工具，拒绝未校验安装。' >&2; exit 2; fi
  [ "$actual" = "$expected" ] || { printf '%s\n' '运行包SHA-256校验失败，拒绝安装。' >&2; exit 2; }
  mkdir "$staging/unpacked"
  unzip -q "$staging/runtime.zip" -d "$staging/unpacked"
  [ -f "$staging/unpacked/photobook-engine/photobook-engine" ] || exit 2
  chmod +x "$staging/unpacked/photobook-engine/photobook-engine"
  printf '%s\n' "$expected" > "$staging/unpacked/.ready"
  if [ -e "$target" ]; then
    [ -f "$target/.ready" ] || { printf '%s\n' '现有运行包不完整，请保留诊断后重新安装。' >&2; exit 2; }
  else mv "$staging/unpacked" "$target"; fi
  rm -rf "$staging"; rm -f "$install_lock/pid"; rmdir "$install_lock"; trap - EXIT HUP INT TERM
fi
exec "$engine" "$@"
