# shellcheck shell=bash
# Shared helpers for scripts/cloud/ops/*.sh. Sourced, never executed directly.
#
# Every ops script: takes --region or falls back to $AWS_REGION, refuses to
# run unless `aws sts get-caller-identity` succeeds, and (when it targets an
# EC2 instance) verifies that instance carries tag Project=mcp-gateway-demo
# before doing anything to it.

# Strips --region VALUE / --region=VALUE out of "$@", leaving the rest in
# REMAINING_ARGS and the resolved region (or "") in REGION.
ops_parse_region() {
  REGION="${AWS_REGION:-}"
  REMAINING_ARGS=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --region)
        REGION="$2"
        shift 2
        ;;
      --region=*)
        REGION="${1#--region=}"
        shift
        ;;
      *)
        REMAINING_ARGS+=("$1")
        shift
        ;;
    esac
  done
}

# Sets AWS_REGION_ARGS array for use as `aws ... "${AWS_REGION_ARGS[@]}"`.
ops_region_args() {
  AWS_REGION_ARGS=()
  if [ -n "${REGION:-}" ]; then
    AWS_REGION_ARGS=(--region "$REGION")
  fi
}

# Refuses to continue unless AWS credentials resolve. Sets ACCOUNT_ID.
ops_require_identity() {
  ops_region_args
  if ! ACCOUNT_ID=$(aws sts get-caller-identity "${AWS_REGION_ARGS[@]}" --query Account --output text 2>&1); then
    echo "refusing to continue: aws sts get-caller-identity failed: ${ACCOUNT_ID}" >&2
    exit 1
  fi
}

# Fails unless the given instance id carries tag Project=mcp-gateway-demo.
ops_require_project_tag() {
  local instance_id="$1"
  ops_region_args
  local tag_value
  tag_value=$(aws ec2 describe-instances "${AWS_REGION_ARGS[@]}" --instance-ids "$instance_id" \
    --query "Reservations[].Instances[].Tags[?Key=='Project'].Value[]" --output text 2>/dev/null || true)
  if [ "$tag_value" != "mcp-gateway-demo" ]; then
    echo "refusing to act: instance $instance_id does not carry tag Project=mcp-gateway-demo (found: '${tag_value}')" >&2
    exit 1
  fi
}

# Finds exactly one running mcpgw-demo instance and prints its id on stdout.
# Fails if zero or more than one match.
ops_find_instance() {
  ops_region_args
  local ids count
  ids=$(aws ec2 describe-instances "${AWS_REGION_ARGS[@]}" \
    --filters "Name=tag:Project,Values=mcp-gateway-demo" "Name=tag:Name,Values=mcpgw-demo" "Name=instance-state-name,Values=running" \
    --query 'Reservations[].Instances[].InstanceId' --output text)
  count=$(wc -w <<< "$ids")
  if [ "$count" -ne 1 ]; then
    echo "expected exactly one running mcpgw-demo instance, found $count: $ids" >&2
    exit 1
  fi
  echo "$ids"
}

# Masks an AWS account id to only its last 4 digits.
ops_mask_account() {
  local account="$1"
  echo "...${account: -4}"
}
