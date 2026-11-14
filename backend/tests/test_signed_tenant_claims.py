from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "erp_stage23_signed_tenant_context",
    BACKEND / "erp" / "db_adapters" / "signed_tenant_context.py",
)
_signed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_signed)
InvalidTenantClaim = _signed.InvalidTenantClaim
sign_tenant_claims = _signed.sign_tenant_claims
verify_tenant_claims = _signed.verify_tenant_claims


class SignedTenantClaimsTests(unittest.TestCase):
    def setUp(self):
        self.secret = b"stage23-test-secret-key-at-least-32-bytes-long"
        self.keys = {"ci-key-1": self.secret}

    def test_signs_canonical_claims_and_verifies(self):
        payload, signature = sign_tenant_claims(
            company_id=101, user_id=7, key_id="ci-key-1", secret=self.secret,
            ttl_seconds=300, now=1_800_000_000,
        )
        claims = verify_tenant_claims(payload, signature, self.keys, now=1_800_000_010)
        self.assertEqual(claims["company_id"], 101)
        self.assertEqual(claims["sub"], 7)
        self.assertEqual(claims["iss"], "erp-global-pro")
        self.assertEqual(json.loads(payload), claims)

    def test_tampered_company_id_is_rejected(self):
        payload, signature = sign_tenant_claims(
            company_id=101, user_id=7, key_id="ci-key-1", secret=self.secret, now=1000
        )
        tampered = payload.replace('"company_id":101', '"company_id":202')
        with self.assertRaises(InvalidTenantClaim):
            verify_tenant_claims(tampered, signature, self.keys, now=1001)

    def test_tampered_signature_is_rejected(self):
        payload, signature = sign_tenant_claims(
            company_id=101, user_id=7, key_id="ci-key-1", secret=self.secret, now=1000
        )
        changed = ("0" if signature[0] != "0" else "1") + signature[1:]
        with self.assertRaises(InvalidTenantClaim):
            verify_tenant_claims(payload, changed, self.keys, now=1001)

    def test_expired_claim_is_rejected(self):
        payload, signature = sign_tenant_claims(
            company_id=101, user_id=7, key_id="ci-key-1", secret=self.secret,
            ttl_seconds=10, now=1000,
        )
        with self.assertRaises(InvalidTenantClaim):
            verify_tenant_claims(payload, signature, self.keys, now=1010)

    def test_claim_with_unknown_key_is_rejected(self):
        payload, signature = sign_tenant_claims(
            company_id=101, user_id=7, key_id="ci-key-1", secret=self.secret, now=1000
        )
        with self.assertRaises(InvalidTenantClaim):
            verify_tenant_claims(payload, signature, {}, now=1001)

    def test_ttl_over_limit_is_rejected(self):
        with self.assertRaises(InvalidTenantClaim):
            sign_tenant_claims(
                company_id=101, user_id=7, key_id="ci-key-1", secret=self.secret,
                ttl_seconds=901, now=1000,
            )

    def test_short_secret_is_rejected(self):
        with self.assertRaises(InvalidTenantClaim):
            sign_tenant_claims(
                company_id=101, user_id=7, key_id="ci-key-1", secret=b"short", now=1000
            )

    def test_future_issued_claim_is_rejected_beyond_clock_skew(self):
        payload, signature = sign_tenant_claims(
            company_id=101, user_id=7, key_id="ci-key-1", secret=self.secret, now=2000
        )
        with self.assertRaises(InvalidTenantClaim):
            verify_tenant_claims(payload, signature, self.keys, now=1969)

    def test_extra_claim_fields_are_rejected(self):
        payload, signature = sign_tenant_claims(
            company_id=101, user_id=7, key_id="ci-key-1", secret=self.secret, now=1000
        )
        claims = json.loads(payload)
        claims["is_admin"] = True
        modified = json.dumps(claims, separators=(",", ":"), sort_keys=True)
        with self.assertRaises(InvalidTenantClaim):
            verify_tenant_claims(modified, signature, self.keys, now=1001)

    def test_signer_rejects_boolean_identifiers(self):
        with self.assertRaises(InvalidTenantClaim):
            sign_tenant_claims(
                company_id=True, user_id=7, key_id="key-1", secret=b"x" * 32
            )

    def test_signer_rejects_float_identifiers(self):
        with self.assertRaises(InvalidTenantClaim):
            sign_tenant_claims(
                company_id=1.5, user_id=7, key_id="key-1", secret=b"x" * 32
            )

    def test_signer_rejects_boolean_ttl_and_clock(self):
        with self.assertRaises(InvalidTenantClaim):
            sign_tenant_claims(
                company_id=1, user_id=7, key_id="key-1", secret=b"x" * 32, ttl_seconds=True
            )
        with self.assertRaises(InvalidTenantClaim):
            sign_tenant_claims(
                company_id=1, user_id=7, key_id="key-1", secret=b"x" * 32, now=True
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
