# Capstone backup plan

Use only captures from a successful, approved synthetic rehearsal. Actual database-backed synthetic captures are now in `live-rehearsal-2026-11-12/`, with results in `LIVE_REHEARSAL_REPORT.md`. They are distinct from earlier mocked UI test screenshots. Check their readability at projection size and recapture at full size if needed.

| Failure | Safe response |
|---|---|
| PostgreSQL unavailable | Stop the live workflow. Do not migrate, reset, provision or use the paused temporary cluster. Present the prepared architecture and labeled rehearsal screenshots. |
| Frontend unavailable | Check the two launch terminals and URL. If not quickly resolved, use the backup images and explain that they are from the rehearsal. Do not reinstall or redesign during the review. |
| Extraction unavailable | Verify mock mode and exact supplied note formatting. If still failing, show rehearsed proposal/Accept/Reject images. Do not insert facts manually to simulate an extraction success. |
| Assessment unavailable | Preserve the honest error or insufficient-information state. Explain authorization, catalog integrity and UNKNOWN handling. Use a labeled prior synthetic result; never invent a drug verdict. |
| Internet unavailable | Local rules still work from the pinned catalog. Show stored source IDs/versions and previously reviewed evidence notes; do not claim a live source fetch. |
| Authentication expires | Sign in privately with the existing account. Never show credentials or disable authentication. |

## Capture after successful rehearsal

1. Synthetic A identifier and six proposals, before confirmation.
2. A's five accepted facts and rejected fatigue proposal.
3. Clinical History with an expanded fact/version/provenance panel.
4. Medications and Clinical Summary (both HbA1c dates and eGFR visible).
5. Verified Records with its recorded-state verification boundary; pair it with the selected source's Clinical History provenance capture.
6. A's three medication cards with synthetic notice, patient identifier and missing-information text.
7. One expanded DailyMed source/rule and exact bound fact versions.
8. B's identifier and different three-card result.
9. An empty/unknown assessment from an approved empty synthetic scenario, if available; never erase records to obtain it.

Use names such as `rehearsal-A-cards.png` and annotate the actual rehearsal time in a separate note. Review every capture for tokens, login identities, unrelated patients and accidental real information before projecting. If no live rehearsal succeeds, state that limitation and present the offline test results and rule design as implementation evidence only.
