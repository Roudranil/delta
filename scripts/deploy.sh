#!/usr/bin/env bash

set -e

usage() {
  cat <<EOF
Usage: $(basename "$0") [--dev|--local|--prod|--help]

Options:
  --dev     Link .env.dev to .env
  --local   Link .env.local to .env
  --prod    Link .env.prod to .env
  --help    Show this help message
EOF
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$#" -ne 1 ]; then
  usage
  exit 1
fi

case "$1" in
  --dev)
    TARGET=".env.dev"
    ;;
  --local)
    TARGET=".env.local"
    ;;
  --prod)
    TARGET=".env.prod"
    ;;
  --help)
    usage
    exit 0
    ;;
  *)
    echo "Unknown option: $1"
    usage
    exit 1
    ;;
esac

cd "$ROOT_DIR"

if [ ! -e "$TARGET" ]; then
  echo "Missing target file: $TARGET"
  exit 1
fi

ln -sf "$TARGET" .env

echo "Linked $TARGET to .env"
