#!/bin/bash
# Test: instance portal HTTP endpoint responds.
source "$(dirname "$0")/../lib.sh"

# Portal is not expected in serverless mode
is_serverless && test_skip "portal not expected in serverless mode"

wait_for_url "http://127.0.0.1:11111/" 30 || test_fail "portal not responding on port 11111 after 30s"

# Check that the portal returns HTML
body=$(curl -sf http://127.0.0.1:11111/ 2>/dev/null)
[[ "$body" == *"<html"* ]] || test_fail "portal response does not contain HTML"

# Check /get-applications returns valid JSON
config=$(curl -sf http://127.0.0.1:11111/get-applications 2>/dev/null) || test_fail "/get-applications request failed"
# Verify it is valid JSON
echo "$config" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null \
    || test_fail "/get-applications did not return valid JSON"

test_pass "portal responds on :11111 with valid HTML and JSON apps"
