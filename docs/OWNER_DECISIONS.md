# Owner decisions still open

This list records decisions the repository cannot make. Nothing here is an approval.
Sources: [ADR 0001](adr/0001-single-vps-production-topology.md),
[ADR 0010](adr/0010-production-provider-readiness.md), and
[docs/SECURITY.md](SECURITY.md) section 17. Decision 8 in ADR 0010 stays open.

## 1. Applicable jurisdiction

ADR 0010 leaves jurisdiction unresolved. The owner must name the legal jurisdiction
that governs member, staff, and payment records for GymPilot MM.

Unblocks: privacy text, retention, and any claim that production data handling
is lawful.

## 2. Privacy and data-protection obligations

No privacy notice, terms, or processor agreement is approved in the repository.
The owner must state which privacy obligations apply and who the controller is.

Unblocks: the security release gate item for retention, privacy, and terms.

## 3. Personal-data retention

ADR 0010 plans Block Public Access, SSE-S3, and versioning only. Current-object
retention and noncurrent-version expiry are not approved and are not configured
as lifecycle rules.

Unblocks: an S3 lifecycle rule, if the owner wants one.

## 4. Database and object-storage residency

The running application bucket and backup bucket are in `ap-southeast-1`.
ADR 0010 selects that region and still requires a residency decision. The owner
must accept `ap-southeast-1` or name a different region.

Unblocks: treating the current buckets as the approved residency.

## 5. Object lifecycle and backup retention

pgBackRest retention in the backup configuration is 14 full backups, 14
differential backups, and 14 archive sets. ADR 0010 says this retention is not
legal approval. The owner must accept those numbers or replace them.

Unblocks: describing backup retention as an approved policy.

## 6. Object replication

ADR 0010 leaves object replication open. Database backup does not copy object
bytes. The owner must say whether application-bucket replication is permitted.

Unblocks: any replication or cross-region object copy.

## 7. RPO and RTO targets

ADR 0001 sets planning targets of RPO at or under 15 minutes and RTO at or under
4 hours. They are restore targets, not a high-availability promise. The owner
must accept those targets or replace them. A database restore drill is evidence
of the database step only. It is not, by itself, acceptance of the targets.

Unblocks: treating the targets as operational commitments.

## 8. Gemini and member or financial data

Production `ASSISTANT_ENABLED` is unset and there is no production Gemini key.
The staff assistant can send a staff message, a short gym brief, and the tool
arguments the model requests to Google Gemini. The owner must decide whether
Gemini may process member, payment, and salary data for the gyms that will use
the assistant.

Unblocks: setting a production `GEMINI_API_KEY` and turning the assistant on.

## 9. First release scope

The owner must say whether the first release includes the member store apps,
expiry push, and the assistant. The staff site can keep the assistant off and
keep `membership_expiry_push_enabled` off. The in-app inbox does not need push.

Unblocks: Firebase, APNs, store signing, and assistant activation work.

## 10. Penetration test and waiver

`docs/SECURITY.md` section 17 requires an independent penetration test covering
IDOR, tenant isolation, privilege escalation, JWT, files, and financial
workflows, plus the production-like pooling run. Repository tests are not that
test. The owner must commission the test or record a waiver and who has
authority to waive it.

Unblocks: closing the security release gate.
