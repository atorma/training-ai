#! /bin/sh
set -e

get_commit_hash() {
  git rev-parse --short HEAD
}