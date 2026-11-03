#!/usr/bin/env bash
set -euo pipefail

paper_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$paper_dir"
mkdir -p build
export TEXINPUTS="$paper_dir/template//:${TEXINPUTS:-}"

local_tex_runner="/home/lbh/.tmp/omt-paper-tools/tex-run"
if [[ -x "$local_tex_runner" ]]; then
    latex_command=("$local_tex_runner" xelatex)
elif command -v xelatex >/dev/null 2>&1; then
    latex_command=(xelatex)
else
    printf '%s\n' '需要 XeLaTeX：请安装包含 ctex、Fandol、algorithm 和 algpseudocode 的 TeX Live。' >&2
    exit 1
fi

for pass in 1 2 3; do
    printf '编译 LaTeX（%s/3）\n' "$pass"
    if ! "${latex_command[@]}" -interaction=nonstopmode -halt-on-error \
        -file-line-error -output-directory=build main.tex >"build/compile-$pass.txt" 2>&1; then
        tail -n 70 "build/compile-$pass.txt" >&2
        exit 1
    fi
done

cp -- build/main.pdf main.pdf
printf 'PDF：%s/main.pdf\n' "$paper_dir"
