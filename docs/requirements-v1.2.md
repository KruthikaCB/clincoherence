# ClinCoherence MVP Software Requirements Specification v1.2

Status: **Approved MVP requirements baseline — implementation subject to stated dependencies**

## 1. Purpose, sources, and boundaries

ClinCoherence is a unified, patient-specific longitudinal clinical LLM verification system, initially for Type 2 Diabetes Mellitus (T2DM). It assists users and clinicians; it does not replace clinical authority or guarantee clinical correctness.

Sources reviewed: repository `README.md`, `docs/architecture.md`, the approved project decisions and final review clarifications, and local excluded dataset notes: `lionic_terminology/README.md`, `rxnorm_capstone/HANDOFF.md`, `dailymed_corpus_final/limitations_and_open_questions.md`, and `MIMIC_Demo_Audit/limitations.md` under `clincoherence/capstone datasets/`.

No earlier standalone approved requirements specification was available. The local `lionic_terminology/ClinCoherence_Decisions_Summary.pdf` was located, but its text could not be extracted with available tooling. Its contents and approval status are not asserted here. Dataset reports are supporting material, not approval to use their contents as runtime evidence. Proposed rules in those reports remain proposals.

This baseline preserves valid existing requirements and records the approved change from Public/Practitioner to authenticated Patient/Practitioner modes. The architecture documentation is aligned with this baseline; README still contains the historical access model pending a separate alignment change. The Phase 0 scaffold does not implement the workflows below. Requirements approval does not authorize application changes in the current documentation task.

“Shall” denotes a requirement. Acceptance criteria describe required future behavior, not tests already implemented. This document specifies behavior without prescribing database models or API contracts.

## 2. Functional requirements

| ID | Requirement |
|---|---|
| FR-01 | The unified system shall offer two authenticated modes: Patient and Practitioner. An unauthenticated user shall not access patient records or patient-specific conversations. |
| FR-02 | Each patient shall have an isolated longitudinal record and patient-specific conversations. Every patient-state read, write, retrieval, and verification operation shall be bound to the intended patient. |
| FR-03 | Each practitioner shall have an individual account. Patient-record access shall require both practitioner authorization and that patient's active consent for that specific practitioner. Authentication alone shall not grant access. |
| FR-04 | A practitioner shall initiate onboarding through a copyable, expiring, single-use invitation link. Invalid, expired, or already redeemed invitations shall not activate an account or grant access. Successful redemption shall consume the invitation once, including concurrent redemption attempts. |
| FR-05 | Patient identity verification and account activation shall be separate from practitioner consent. The local research MVP may use a controlled synthetic-user identity-verification flow, explicitly labeled prototype-only and not production-grade identity verification. The flow shall bind a synthetic account to the intended synthetic patient without treating invitation possession as identity proof. Invitation creation, possession, redemption, identity verification, or account activation shall never implicitly grant practitioner access. Production identity assurance remains a dependency before production onboarding. |
| FR-06 | An activated patient shall be able to explicitly grant and revoke access for individual practitioners. Each consent decision shall identify the practitioner and patient concerned. Multiple separately authorized and consented practitioners may access the same patient record. |
| FR-07 | Revocation shall immediately deny further record access for the affected practitioner, including through existing authenticated sessions. Current access shall be checked server-side for every protected operation and again before committing writes or releasing patient data from in-flight work. Revoked access shall not be restored by an old token, cached permission, or pending request. |
| FR-08 | Every LLM-extracted candidate fact shall appear as an individual confirmation card inside its patient-specific chat. Each card shall identify the proposed fact and its source sufficiently for individual review. Confirming one card shall not confirm another. |
| FR-09 | Patients shall confirm facts extracted from their own conversations; practitioners shall confirm facts extracted from their own conversations. Confirmation shall require current authorization and permission for the information category. No actor shall confirm on another conversation participant's behalf. |
| FR-10 | Only individually confirmed LLM-extracted facts shall enter accepted persistent patient state. Pending, rejected, or invalid candidates shall never enter accepted patient state or be silently relied on as facts. A conversation may continue using previously accepted facts while proposals remain pending or rejected, provided all authorization and verification checks pass. Confirmation shall not bypass validation or role permissions. Candidate/chat/audit storage shall distinguish proposals from accepted state. |
| FR-11 | Valid structured entries may enter persistent patient state after authorization and validation without a separate LLM-extraction confirmation step. The system shall preserve that structured-entry provenance rather than falsely recording LLM confirmation. |
| FR-12 | Patients shall manage their own patient-reported facts. Authorized and consented practitioners shall manage practitioner-entered clinical facts within their patient-specific permissions. Neither role shall silently overwrite the other's information. Patient reports about clinical matters shall remain patient-reported; practitioner entries shall retain their clinical authorship. Corrections shall follow FR-13 and FR-14. Shared-record access shall not automatically grant cross-participant transcript access. |
| FR-13 | Corrections shall create linked versions and preserve the original information, original and correcting authors, timestamps, and provenance. Historical versions shall remain distinguishable from current accepted state; corrections shall not silently erase or rewrite prior facts. |
| FR-14 | Access grants, revocations, confirmations, corrections, and clinical interactions shall be auditable. Events shall identify the actor, patient, action, time, affected item/version or interaction, provenance, and result as applicable. Audit history shall be subject to access control. |
| FR-15 | The system shall use a model-agnostic provider boundary with a mock LLM provider for initial development and repeatable fixtures. Local/cloud providers are later integrations, not prerequisites for the initial mock implementation. |
| FR-16 | Both modes' patient-specific clinical conversations shall use the planned 11-stage pipeline in Section 5, including independent runtime verification before a drafted answer is released. The drafting LLM's self-assessment alone shall not constitute verification. |
| FR-17 | The decision gate shall produce PASS, WARN, CLARIFY, or REFUSE with a recorded reason and relevant provenance. Outcome semantics are defined in Section 5. |
| FR-18 | Runtime evidence sources, versions, and intended uses shall undergo review and explicit approval before use. Retrieval and verification shall retain source identifiers, version/effective date where available, and supporting location or evidence-unit identifiers. Local dataset availability shall not imply approval. |
| FR-19 | The MVP shall support text-only interactions in the T2DM domain. It shall not autonomously diagnose, prescribe, or execute medication changes; clinical decisions and prescribing remain with licensed practitioners. |

## 3. Authorization and patient-state isolation

All permissions below are enforced server-side and limited to the requested patient. A visible mode selector or client-supplied patient identifier shall not confer authority.

| Actor/state | Permitted behavior | Boundary |
|---|---|---|
| Unauthenticated user | Follow onboarding entry flow and authenticate | No patient-record or conversation access |
| Patient pending verification/activation | Complete identity verification and account activation | No practitioner consent is inferred; invitation is not a record-access credential |
| Authenticated, activated patient | Access own record and conversations; manage own patient-reported facts and versioned corrections; confirm own extracted facts; grant/revoke individual practitioner access | No access to another patient's state; no silent overwrite of practitioner-entered clinical facts; no automatic access to another participant's transcripts |
| Authenticated, authorized practitioner with active consent | Access that patient's shared record; conduct patient-specific conversations; manage practitioner-entered clinical facts and versioned corrections within permissions; confirm own extracted facts | No silent overwrite of patient-reported facts; consent is patient- and practitioner-specific; no confirmation on another participant's behalf or automatic access to their transcripts |
| Practitioner without consent or after revocation | Initiate permitted onboarding/access workflow | No patient-record reads, writes, history retrieval, or patient-specific generated responses |

Shared-record access shall not automatically grant access to another participant's conversation transcript. Cross-participant transcript access is denied unless a separate explicit policy is approved before that access is implemented; no such access is granted by this baseline. Corrections in either information category remain versioned and auditable. No additional role or administrative override is introduced.

Patient identifiers and current permissions shall scope database access, retrieval, conversation context, caches, and verification inputs. One patient's information shall not enter another patient's prompt, result, record, or audit view. Multiple practitioners shall work against the same patient's current versioned record; concurrent changes shall not silently overwrite one another.

## 4. Required workflows

### Onboarding and consent

1. An authenticated practitioner initiates onboarding and obtains a copyable invitation with an enforced expiry and single-use status.
2. The patient follows the link. The system validates the invitation and completes identity verification and activation. For local research, a controlled synthetic-user flow may be used and shall be visibly labeled prototype-only, not production-grade identity verification. Redemption and activation do not grant practitioner access; production onboarding requires separately approved production identity assurance.
3. The activated patient makes a separate explicit consent decision identifying the practitioner. Only an active grant together with practitioner authorization enables access.
4. The patient can independently grant access to additional authorized practitioners and revoke any one grant without affecting the others.
5. After revocation takes effect, the system rejects subsequent protected operations and prevents pending operations from disclosing or committing patient data. Audit history is retained. Revocation cannot retract information already legitimately delivered before it took effect.

### Fact confirmation and structured entry

1. A message is associated with the authenticated actor, conversation, and patient; authorization and input validation occur before extraction.
2. Extraction produces individual proposed-fact cards inside that chat. Proposals remain distinct from accepted patient state.
3. The conversation's author reviews each fact individually. Only an explicit confirmation of that specific candidate, followed by validation and permission checks, may accept it. Editing a proposal shall not carry an earlier confirmation over to changed content.
4. Pending/rejected facts never enter accepted longitudinal state. The conversation may continue using previously accepted facts if authorization and verification checks pass, without silently relying on pending/rejected proposals. A patient-originated fact remains patient-reported, even when it describes a clinical measurement or medication.
5. A direct structured entry follows authorization and validation, then state/version/audit persistence; it does not require a fabricated extraction-card step.

### Corrections

1. An authorized actor selects an accepted item within their information permissions and submits a correction.
2. The system validates the correction, checks the current version and access, creates a successor version, and records its author, time, provenance, and relationship to the preserved original.
3. If the correction itself is extracted by an LLM, individual confirmation is required. A valid direct structured correction follows FR-11.
4. Subsequent verification uses current accepted state with access to relevant authorized history. Conflicting or concurrent changes shall remain explicit rather than silently replacing each other.

## 5. Evidence and independent runtime verification

The planned stage order is preserved. Acceptance of an extracted fact may pause for confirmation; this does not require the entire conversation to stop. A conversation may proceed with previously accepted facts when all authorization and verification checks pass. Pending/rejected proposals shall not enter accepted state, bypass stage 4, or be silently relied on as factual context for drafting or verification.

| Stage | Responsibility |
|---|---|
| 1 | Authentication and authorization, including patient scope and current consent |
| 2 | Input validation for text-only interactions and permitted entries |
| 3 | Candidate fact extraction |
| 4 | Fact validation and individual confirmation, or authorized validated structured-entry handling |
| 5 | Patient state management |
| 6 | Longitudinal event and version history |
| 7 | Approved clinical evidence retrieval |
| 8 | LLM response drafting, initially using the mock provider |
| 9 | Independent runtime verification against patient state/history and approved evidence |
| 10 | PASS / WARN / CLARIFY / REFUSE decision gate |
| 11 | Response and audit persistence |

The verifier shall assess patient-specific longitudinal consistency, relevant evidence support, target attribution, and qualifiers needed by approved rules. Verification records shall identify the patient-state version, draft/provider, evidence references, and verifier/rule version used. Missing or ambiguous evidence shall not be represented as proven clinical falsity or silently treated as successful verification.

Terminology normalization is not clinical evidence by itself. Evidence for an ingredient shall not automatically substantiate a specific product, formulation, dose, or route. Original values, units, and provenance shall be preserved; uncertain mappings or conversions shall not silently manufacture clinical certainty. Dataset-derived clinical thresholds and detailed rule-to-outcome mappings require review before activation.

| Outcome | Required behavior |
|---|---|
| PASS | Release a response that passed the configured checks. PASS does not guarantee clinical correctness or absolute safety. |
| WARN | Release the response with explicit, visible warnings and the reasons for them. |
| CLARIFY | Withhold the draft answer and request clarification from the current patient or practitioner. |
| REFUSE | Block the draft answer because of detected safety hazards or irreconcilable contradictions, and explain the refusal appropriately. |

A verification failure or unavailable required evidence shall not produce PASS by default. The system shall withhold an unverified draft; selecting CLARIFY versus REFUSE for specific failure cases requires the approved rule policy. Audit persistence shall record the interaction and decision without turning blocked output or unconfirmed proposals into accepted patient facts. Consent checks also apply at final persistence and release.

## 6. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-01 Security | Protected operations shall deny access by default unless role, patient scope, and current consent permit them. Invitation links shall resist guessing, enforce expiry and atomic single use, and shall not be exposed in ordinary logs. Secrets and local databases shall remain outside source control. |
| NFR-02 Integrity | State/version/audit changes for a successful mutation shall be consistent: no accepted change without its required history and audit event. Repeated confirmation or request retries shall not duplicate accepted facts. Conflicting updates shall not silently lose prior work. |
| NFR-03 Auditability | Authorized review shall reconstruct accepted-state changes and verification decisions from preserved authorship, timestamps, provenance, and versions. Ordinary corrections shall not edit prior audit events. |
| NFR-04 Data protection | Development shall use synthetic or appropriately de-identified data, with provenance and suitability review before use. Identifiable real patient data is outside development scope. De-identification shall not be presumed from a filename or dataset location. Existing dataset exclusions shall remain intact. |
| NFR-05 Reproducibility | Initial provider and verification fixtures shall be repeatable using mock inputs and pinned evidence/rule/state versions. Tests shall cover permission denial, revocation, isolation, fact confirmation, corrections, and decision gating. |
| NFR-06 Usability | Users shall be able to identify the patient context, proposed versus accepted facts, individual confirmation actions, practitioner consent status, and verification outcomes without confusing WARN/PASS with clinical authorization. |
| NFR-07 Failure behavior | Failed authorization, validation, confirmation, or verification shall not silently commit facts or release a blocked draft. Error handling shall not disclose another patient's information. |

No numeric latency, capacity, uptime, retention period, or regulatory certification target has been approved; none is invented here. “Immediate” revocation is an authorization invariant, not a deferred cache-expiry target.

## 7. Core acceptance criteria

| ID / requirements | Observable acceptance criterion |
|---|---|
| AC-01 / FR-01–03 | Unauthenticated requests and patient A's requests for patient B's record/conversation are denied. A logged-in practitioner without both authorization and active patient consent is denied. |
| AC-02 / FR-04–05 | A practitioner can copy a valid invitation. Expired or redeemed links are rejected. Two simultaneous redemptions yield at most one success. The local research flow binds a controlled synthetic user to the intended synthetic patient and is labeled prototype-only, not production-grade identity verification. Redemption and activation leave the initiating practitioner unable to read the record until separate explicit consent is granted. |
| AC-03 / FR-06–07 | Patient grants access to practitioners P1 and P2, then revokes P1. P1's existing session is immediately denied reads and writes; pending work cannot release data or commit a change after revocation. P2 remains authorized. |
| AC-04 / FR-08–10 | A message yielding three facts renders three individual chat cards. Confirming one admits only that validated, permitted fact. Pending or rejected candidates never enter accepted state. With checks passing, a later turn can proceed using previously accepted facts without relying on pending/rejected proposals. Another actor cannot confirm them; retrying confirmation does not duplicate state. |
| AC-05 / FR-11–12 | A valid authorized structured entry persists without an extraction card. Invalid or unauthorized entries do not persist. A patient manages only their own patient-reported facts; an authorized and consented practitioner manages practitioner-entered clinical facts. Neither silently overwrites the other's information. Shared-record access alone does not permit reading another participant's transcript. |
| AC-06 / FR-13–14 | A correction creates a successor version; original content, authors, timestamps, and provenance remain available to authorized review. Concurrent changes cannot silently overwrite one another. Grants, revocations, confirmations, corrections, and interactions have attributable audit events. |
| AC-07 / FR-02, FR-16 | Interleaved conversations for two patients use only each patient's permitted accepted state and history, including retrieval and cached context. Unconfirmed candidates never appear as accepted state in verifier inputs. |
| AC-08 / FR-15–17 | Mock scenarios exercise all 11 stages and all four outcomes. PASS releases; WARN includes warnings; CLARIFY withholds and asks; REFUSE blocks and explains. Draft self-approval or verifier failure cannot bypass the gate. |
| AC-09 / FR-18 | An unapproved evidence source/version cannot be used at runtime. A supported decision records the evidence location/version, patient-state version, provider, and verifier/rule version. Unsupported product mappings or missing qualifiers do not silently pass. |
| AC-10 / FR-19, NFR-04 | Audio/attachment inputs are rejected for the text-only MVP. Scenarios requesting autonomous diagnosis, prescribing, or medication changes do not cause autonomous clinical action. Development fixtures have reviewed synthetic/de-identified provenance. |

## 8. Explicit MVP exclusions

- Unauthenticated Public-mode clinical chat; the MVP's two clinical modes require authentication.
- Emergency consent overrides or practitioner access without explicit active consent.
- Autonomous diagnosis, prescribing, or medication changes.
- Audio, attachments, and multimodal clinical inputs.
- Disease-domain expansion beyond the initial T2DM scope.
- Identifiable real patient development data; automatic ingestion of excluded local datasets.
- Mandatory real-LLM integration for the initial mock implementation, or claims of clinical correctness from smoke tests or dataset audits.

No additional product features are implied by these requirements.

## 9. Open decisions and implementation dependencies

The requirements baseline is approved. The following dependencies block only the affected implementation or validation work; they do not reopen the approved initial permission matrix or prevent controlled synthetic-user prototype work:

| Decision needed | Work affected |
|---|---|
| Approve production identity assurance and account-to-patient binding procedures, including practitioner authorization/credential eligibility for production | Production onboarding and identity/credential assurance; the permitted controlled synthetic-user flow is prototype-only |
| Define invitation lifetime and when successful redemption is considered complete during activation | Final onboarding configuration and lifecycle tests |
| Approve a separate explicit transcript-access policy if cross-participant transcript access is later proposed | Any cross-participant transcript access; shared-record access alone continues to deny it |
| Review/approve runtime evidence sources/versions, clinical checks, and outcome mappings, including missing evidence and conflicting qualifiers | Evidence-backed verifier behavior and clinically meaningful outcome fixtures |

Retention and deployment-specific security parameters need definition before deployment; they do not prevent the initial mock workflow design. Existing dataset notes are not proof that sources or clinical rule mappings are approved. If earlier approved documents become available, reconcile them explicitly without silently changing this approved baseline.
