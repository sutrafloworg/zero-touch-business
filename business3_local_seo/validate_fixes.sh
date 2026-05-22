#!/usr/bin/env bash
# Local validation script for the AUDIT_2026-05-20 fixes.
# Run from the business3_local_seo/ directory.
#
# This re-creates everything I tried to verify from the cowork sandbox
# (where a FUSE metadata cache prevented compile-checking Edit-modified files).
set -e

cd "$(dirname "$0")"
echo "==== 1. Python syntax check on every changed file ===="
for f in \
  config.py orchestrator.py followup_sequence.py testimonial_outreach.py \
  webhook_server.py quota_warning.py \
  agents/scanner_agent.py agents/outreach_agent.py agents/fulfillment_agent.py \
  agents/analyzer_agent.py agents/json_store.py agents/suppression.py
do
  python3 -m py_compile "$f" && echo "  OK   $f" || echo "  FAIL $f"
done

echo ""
echo "==== 2. Import smoke test ===="
python3 -c "
import sys; sys.path.insert(0, '.')
from agents import json_store, suppression, scanner_agent, outreach_agent, \
    fulfillment_agent, analyzer_agent
import config
import followup_sequence, testimonial_outreach, quota_warning, webhook_server
print('  all modules import cleanly')
"

echo ""
echo "==== 3. Functional tests ===="
python3 -c "
import sys, tempfile, json, os
sys.path.insert(0, '.')

# json_store
from agents.json_store import atomic_write_json, safe_load_json
from pathlib import Path
with tempfile.TemporaryDirectory() as td:
    p = Path(td)/'t.json'
    atomic_write_json(p, {'a':1})
    assert safe_load_json(p, {}) == {'a':1}
    assert not list(p.parent.glob('*.tmp'))
print('  atomic_write_json + safe_load_json: OK')

# suppression
import agents.suppression as sup
with tempfile.TemporaryDirectory() as td:
    sup.SUPPRESSION_FILE = Path(td)/'sup.json'
    assert sup.suppress('a@b.com', 't') is True
    assert sup.suppress('a@b.com', 't') is False
    assert sup.is_suppressed('A@B.COM') is True
    assert sup.is_suppressed('') is True
    assert sup.unsuppress('a@b.com') is True
    assert sup.is_suppressed('a@b.com') is False
print('  suppression: OK')

# quota_warning
from datetime import datetime, timezone
from quota_warning import _days_until_reset
from datetime import datetime as DT
assert _days_until_reset(DT(2026,5,20,tzinfo=timezone.utc)) == 2
assert _days_until_reset(DT(2026,5,22,tzinfo=timezone.utc)) == 31
assert _days_until_reset(DT(2027,1,1,tzinfo=timezone.utc)) == 21
print('  quota_warning._days_until_reset: OK')

# config UTM params
import config
assert 'utm_source=email' in config.PAYMENT_URL_AUDIT
assert 'utm_source=email' in config.PAYMENT_URL_MONITORING
assert hasattr(config, 'VALUESERP_API_KEY')
print('  config UTM + VALUESERP_API_KEY: OK')

# webhook security
import importlib
ws = importlib.import_module('webhook_server')
# When STRIPE_WEBHOOK_SECRET is empty, stripe_webhook should refuse with 503
# (Just verify the helper exists; full webhook test needs a Flask test client)
assert hasattr(ws, '_check_admin_auth')
print('  webhook_server hardening present: OK')
"

echo ""
echo "==== 4. Data file integrity ===="
python3 -c "
import json
for f in ['data/customers.json','data/pending_reports.json','data/rankings_history.json','data/search_usage.json','data/state.json','data/cities.json']:
    with open(f) as fp: json.load(fp)
print('  all JSON data files parse OK')
"

echo ""
echo "==== 5. Workflow YAML validity ===="
python3 -c "
import yaml
for f in ['../.github/workflows/local_seo_weekly.yml',
         '../.github/workflows/testimonial_outreach.yml',
         '../.github/workflows/quota_warning.yml']:
    with open(f) as fp: yaml.safe_load(fp)
    print(f'  {f}: OK')
"

echo ""
echo "==== 6. Suppression CLI smoke test ===="
python3 agents/suppression.py add user@domain.com --reason "placeholder_known_bad"
python3 agents/suppression.py add example@domain.com --reason "placeholder_known_bad"
python3 agents/suppression.py check user@domain.com
python3 agents/suppression.py list

echo ""
echo "==== 7. Follow-up sequence dry-run ===="
python3 followup_sequence.py --dry-run

echo ""
echo "==== 8. Testimonial outreach dry-run ===="
python3 testimonial_outreach.py --dry-run --count 3

echo ""
echo "==== ALL DONE ===="
