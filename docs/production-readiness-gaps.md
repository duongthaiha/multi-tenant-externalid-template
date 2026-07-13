# Production Readiness Gaps

The POC intentionally proves a secure tenant-isolation pattern without implementing all production controls.

## Deferred from the POC

| Area | Gap |
|---|---|
| Availability | No production SLA target or multi-region active-active design. |
| Data resilience | No full backup, restore, or disaster recovery exercise. |
| Data governance | No customer-managed keys or formal data-retention policy. |
| Identity governance | Conditional Access, lifecycle workflows, access reviews, and production onboarding are not complete. |
| CI/CD | Automated pipelines are deferred; `azd up` is the repeatable POC path. |
| Security testing | Full OWASP review, penetration test, and threat-model signoff are not complete. |
| Operations | Production incident management, SLOs, and on-call procedures are not defined. |
| Cost management | Production SKU sizing, budgets, and tenant-level chargeback are not finalized. |
| Compliance | Formal FSI compliance mapping is out of scope. |

## Required before production

- Confirm regional and data-residency requirements.
- Define production tenant onboarding, suspension, deletion, and data export workflows.
- Add automated CI/CD with environment promotion controls.
- Complete threat modeling and security testing.
- Define backup, restore, and disaster recovery objectives.
- Choose production SKUs and scaling rules.
- Add operational dashboards, SLOs, alerts, and incident response procedures.
