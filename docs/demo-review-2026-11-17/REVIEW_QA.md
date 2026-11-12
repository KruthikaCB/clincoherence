# Examiner questions and concise answers

1. **What does ClinCoherence do?** It organizes patient-scoped longitudinal facts, supports supervised extraction and checks record consistency. The capstone adds limited synthetic medication label checks.
2. **Does it prescribe?** No. It emits no dose, order, initiation, discontinuation or individualized safety clearance.
3. **Why only three drugs?** A deliberately bounded, testable demonstration is easier to verify than an unrestricted drug recommender.
4. **Does an LLM choose the drug?** No. Deterministic code evaluates pinned rules. The LLM belongs only to the existing extraction pathway.
5. **Is this presentation actually running Ollama?** The repeatable script uses the deterministic mock extraction provider. Local Ollama is implemented separately and must not be claimed as running when mock is selected.
6. **What prevents invented extracted facts?** Candidate schema, exact source spans and grounding checks, followed by individual human Accept/Reject. Grounding does not prove that the source note is clinically true.
7. **Does Accept mean clinician approval?** No. Existing speaker confirmation is distinct from M8 clinician review; 0011 is unapplied and its routes are default-off.
8. **Why can these cards run without clinician approval?** They are restricted to explicitly synthetic educational demo records and prominently labeled as such. They are not live clinical recommendations.
9. **Can a patient access this new endpoint?** It requires an authorized practitioner. Authentication and existing patient-scope checks precede record loading.
10. **How is consent enforced?** Existing ordinary-patient consent rules remain intact. Accountless synthetic demo patients have an existing restricted owner path; no pretend patient consent is created. The new assessment refuses ordinary patients afterward.
11. **How do you prevent cross-patient results?** Database queries and fact validation bind the patient ID. The UI validates patient/source IDs and discards responses on context changes.
12. **What if a response arrives after switching patients?** It is aborted/discarded, and prior cards are cleared. No result is carried into the next workspace.
13. **What happens to rejected proposals?** They are not current accepted facts and cannot enter the new evaluator. The demo rejects the deliberately incorrect fatigue proposal.
14. **What happens after a correction?** Existing append-only correction history is preserved, and only the current accepted version is evaluated. Old values do not retain eligibility through their previous version IDs.
15. **How is the latest lab selected?** By observation time among current accepted roots, not the time someone entered it. Conflicting values at the latest time yield UNKNOWN.
16. **What does missing mean?** UNKNOWN. No recorded allergy, medication or history is not a negative finding or an assumption of normality.
17. **Why no green positive card?** These few checks do not establish comprehensive eligibility or separate clinician approval. Even untriggered rules leave information insufficient.
18. **Which evidence is used?** Three specified DailyMed SPL versions, with source IDs, sections, dates and a pinned local catalog hash. Independent clinical review remains pending.
19. **Is ADA guidance implemented?** No ADA treatment algorithm or ranking is active in this milestone. It is not implied by the Type 2 Diabetes scope.
20. **Do you use LOINC and RxNorm?** Existing adapters remain available elsewhere. This narrow engine uses the prototype's explicit laboratory codes and named label checks; it does not claim fresh terminology mapping.
21. **Is the empagliflozin low-eGFR warning a universal contraindication?** No. This check concerns the label's glycemic-control limitation; it does not evaluate heart-failure or CKD indications.
22. **Does the metformin record prove current therapy?** No. It is recorded context. Medication reconciliation and indication must be established by a clinician.
23. **How are MTC/MEN2 histories recognized?** Only explicitly documented controlled phrases, including specified negatives. Conflicting or unrecognized prose remains UNKNOWN.
24. **What if the evidence file changes?** The pinned hash fails, all rule verdicts are withheld and the cards show insufficient information. No silent web/LLM fallback exists.
25. **Can the assessment be traced later?** The response carries source/version IDs and catalog hash. It does not create a new durable assessment audit event; existing record workflows retain their existing audit behavior.
26. **Has this been clinically validated?** No. Software tests and source verification are not prospective clinical validation or medical-device approval.
27. **Which tests were not run?** PostgreSQL integration tests, migration/trigger/concurrency validation and actual database-backed rehearsal. Skips are reported separately.
28. **How is patient privacy handled in this demonstration?** Only invented patients and notes; memory-only session state, no-store response handling, and no real patient screenshots. This is not a compliance certification.
29. **What is the next engineering milestone?** Separately validate M8 clinician review and its database protections, then design clinician-approved evidence-backed decisions with a durable audit trail and broader validated rules.
30. **What happens if the live demo fails?** Use labeled screenshots captured from a successful synthetic rehearsal and explain the failure honestly; do not fabricate a live result or relax safeguards.
