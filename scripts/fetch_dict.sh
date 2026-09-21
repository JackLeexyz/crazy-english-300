#!/usr/bin/env bash
# 下载 ECDICT 英汉词典源文件（约 63MB），构建脚本需要它来生成单词释义。
# 只在需要重新生成 index.html 时才执行；仓库不保存该大文件。
set -e
cd "$(dirname "$0")/.."
curl -sL --max-time 500 -o ecdict.csv \
  https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv
ls -lh ecdict.csv
