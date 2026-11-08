# Cloudflare R2 for the H3 golden image. Sourced by infra/h3_r2_push.sh and infra/h3_up.sh.
#
# Bucket layout (content-addressed weights, so a new snapshot re-uploads only changed files):
#   <bucket>/weights/<sha256>                    each weight file once, named by its SHA-256
#   <bucket>/snapshots/<stamp>/software.tar.zst  software archive (infra/h3_snapshot_spec.sh)
#   <bucket>/snapshots/<stamp>/software.sha256
#   <bucket>/snapshots/<stamp>/weights.tsv       path<TAB>size<TAB>sha256 for every weight
#   <bucket>/snapshots/<stamp>/SNAPSHOT.txt      base image digest, GPU, repo commit, date
#   <bucket>/snapshots/LATEST                    stamp of the newest complete snapshot
#
# Keys live only in the Mac Keychain (never in chat, the repo or on the server's disk):
#   security add-generic-password -s h3-r2-account-id      -a h3 -w
#   security add-generic-password -s h3-r2-access-key-id   -a h3 -w
#   security add-generic-password -s h3-r2-secret-access-key -a h3 -w
# They reach the server's rclone as environment variables over SSH stdin.

H3_R2_BUCKET="${H3_R2_BUCKET:-h3-studio}"

h3_r2_keychain() {
  security find-generic-password -s "$1" -a h3 -w 2>/dev/null \
    || { echo "Нет ключа «$1» в связке ключей Mac (см. infra/h3_r2_common.sh)" >&2; return 1; }
}

# Prints shell lines that configure an rclone remote named "r2" from environment variables only.
# H3_R2_TEST_DIR switches to a local directory (tests and the local stand), no keys needed.
h3_r2_env_lines() {
  if [[ -n "${H3_R2_TEST_DIR:-}" ]]; then
    printf 'export RCLONE_CONFIG_R2_TYPE=local\nexport H3_R2_ROOT=%q\n' "r2:$H3_R2_TEST_DIR/$H3_R2_BUCKET"
    return
  fi
  local account key secret
  account="$(h3_r2_keychain h3-r2-account-id)" || return 1
  key="$(h3_r2_keychain h3-r2-access-key-id)" || return 1
  secret="$(h3_r2_keychain h3-r2-secret-access-key)" || return 1
  printf 'export RCLONE_CONFIG_R2_TYPE=s3\nexport RCLONE_CONFIG_R2_PROVIDER=Cloudflare\n'
  printf 'export RCLONE_CONFIG_R2_ENDPOINT=%q\n' "https://$account.r2.cloudflarestorage.com"
  printf 'export RCLONE_CONFIG_R2_ACCESS_KEY_ID=%q\nexport RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=%q\n' "$key" "$secret"
  printf 'export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true\nexport H3_R2_ROOT=%q\n' "r2:$H3_R2_BUCKET"
}

# Remote prelude: rclone present (Ubuntu package; R2 needs the Cloudflare provider, rclone >= 1.59).
H3_R2_REMOTE_PRELUDE='set -euo pipefail
export RCLONE_CONFIG=/dev/null  # remote comes from RCLONE_CONFIG_R2_* only
command -v rclone >/dev/null || { apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq rclone zstd >/dev/null; }
command -v zstd >/dev/null || { DEBIAN_FRONTEND=noninteractive apt-get install -y -qq zstd >/dev/null; }
R2="$H3_R2_ROOT"
RC=(rclone --transfers 16 --checkers 32 --multi-thread-streams 8 --retries 5 --low-level-retries 20 --stats 0)'

# Run a remote bash script with the R2 environment on stdin (secrets never on argv or disk).
#   h3_r2_ssh SCRIPT_TEXT ARGS...   (uses $ssh_cmd array set by the caller)
h3_r2_ssh() {
  local script="$1"; shift
  { h3_r2_env_lines || return 1; printf '%s\n' "$H3_R2_REMOTE_PRELUDE"; printf '%s\n' "$script"; } \
    | "${ssh_cmd[@]}" "bash -s -- $*"
}
