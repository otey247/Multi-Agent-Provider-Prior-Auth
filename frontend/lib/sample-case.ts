import type { PriorAuthRequest } from "./types";

export interface ProviderSampleCase {
  id: string;
  title: string;
  specialty: string;
  scenario: string;
  summary: string;
  documentationGaps: string[];
  expectedFindings: string[];
  nextActions: string[];
  request: PriorAuthRequest;
}

export const SAMPLE_CASES: ProviderSampleCase[] = [
  {
    id: "pulmonology-biopsy",
    title: "Advanced Imaging Follow-up → Bronchoscopic Biopsy",
    specialty: "Pulmonology",
    scenario:
      "A PA coordinator is preparing a Medicare submission after interval growth of a pulmonary nodule and PET avidity increased concern for malignancy.",
    summary:
      "Demonstrates a near-submission-ready outpatient pulmonary case with imaging, failed conservative treatment, and specialist documentation already assembled.",
    documentationGaps: [
      "Include the signed CT and PET/CT reports as attachments if they are not already indexed in the chart.",
      "Confirm bronchoscopy scheduling note and consent are linked to the packet before submission.",
    ],
    expectedFindings: [
      "Documentation Completeness Agent should find a mostly complete case package with minimal follow-up needed.",
      "Clinical Evidence Retrieval Agent should highlight interval growth, FDG avidity, smoking history, and failed antibiotic trial.",
      "Policy Matching Agent should find pulmonology-specialty alignment and likely medical necessity support for tissue diagnosis.",
    ],
    nextActions: [
      "Queue for provider signature review.",
      "Submit through the payer portal or ePA workflow once supporting imaging is attached.",
    ],
    request: {
      patient_name: "John Smith",
      patient_dob: "1958-03-15",
      provider_npi: "1720180003",
      diagnosis_codes: ["R91.1", "J18.9", "R05.9"],
      procedure_codes: ["31628"],
      clinical_notes:
        "68-year-old male presenting with persistent right lower lobe pulmonary " +
        "nodule. CT chest (01/15/2026) demonstrates a 1.8 cm spiculated nodule " +
        "in the RLL, increased from 1.2 cm on prior CT (10/12/2025), consistent " +
        "with interval growth over 3 months. PET/CT (01/22/2026) shows FDG " +
        "avidity with SUV max of 4.2, concerning for malignancy.\n\n" +
        "PMH: COPD (mild, GOLD stage I), hypertension, hyperlipidemia. " +
        "40 pack-year smoking history, quit 5 years ago. No prior history of " +
        "malignancy. Family history significant for lung cancer (father, age 72). " +
        "Medications: albuterol inhaler PRN, lisinopril 10 mg daily, " +
        "atorvastatin 20 mg daily. Allergies: NKDA.\n\n" +
        "Physical exam: Vitals BP 132/78, HR 76, RR 16, SpO2 95% on room air. " +
        "Lungs with decreased breath sounds at right base; no wheezing or " +
        "crackles. Remainder of exam unremarkable.\n\n" +
        "Labs (01/20/2026): WBC 9.2 K/uL, Hgb 14.1 g/dL, Platelets 245 K/uL, " +
        "INR 1.0, Creatinine 0.9 mg/dL, BUN 18 mg/dL. Comprehensive metabolic " +
        "panel within normal limits.\n\n" +
        "Pulmonary function tests (01/18/2026): FEV1 78% predicted, FVC 82% " +
        "predicted, FEV1/FVC ratio 0.73, DLCO 71% predicted. Patient is an " +
        "acceptable surgical candidate.\n\n" +
        "Patient completed a 14-day course of amoxicillin-clavulanate with no " +
        "resolution of the nodule. Given the spiculated morphology, interval " +
        "growth, FDG avidity (SUV 4.2), and significant smoking history, there " +
        "is high suspicion for primary lung malignancy per Fleischner Society " +
        "guidelines. Recommend CT-guided transbronchial lung biopsy (CPT 31628) " +
        "for tissue diagnosis. Risks including pneumothorax and bleeding were " +
        "discussed; patient consents to proceed.",
      insurance_id: "MCR-123456789A",
      ordering_provider_name: "Sarah Patel, MD",
      ordering_provider_npi: "1720180003",
      rendering_provider_specialty: "Pulmonology",
      servicing_facility: "North Valley Medical Center - Outpatient Endoscopy",
      payer_name: "Traditional Medicare",
      payer_plan: "Part B",
      urgency: "standard",
      place_of_service: "Hospital Outpatient Department",
      attached_note_types: [
        "Pulmonology consult note",
        "CT chest report",
        "PET/CT report",
        "Pulmonary function test",
      ],
      prior_treatment_history: [
        "14-day amoxicillin-clavulanate trial without resolution",
        "Serial CT surveillance over 3 months with interval growth",
      ],
    },
  },
  {
    id: "oncology-infusion",
    title: "Specialty Drug / Infusion Start of Care",
    specialty: "Oncology",
    scenario:
      "An infusion authorization team is preparing first-cycle biologic therapy for metastatic colorectal cancer after standard chemotherapy progression.",
    summary:
      "Represents a high-value specialty drug workflow where payer policy, line-of-therapy documentation, and biomarker evidence must all align.",
    documentationGaps: [
      "Attach KRAS/NRAS/BRAF biomarker report and most recent oncology treatment roadmap.",
      "Add infusion center tax ID and buy-and-bill site-of-care confirmation for the payer packet.",
    ],
    expectedFindings: [
      "Documentation Completeness Agent should request supporting pathology and biomarker attachments if not listed.",
      "Clinical Evidence Retrieval Agent should surface prior treatment failure and metastatic progression from oncology notes.",
      "Policy Matching Agent should map specialty-drug requirements to line-of-therapy and diagnosis criteria.",
    ],
    nextActions: [
      "Route to oncology pharmacist or infusion pre-cert specialist for final packet QA.",
      "Resubmit with biomarker documentation if the case is flagged as insufficient.",
    ],
    request: {
      patient_name: "Maria Gonzalez",
      patient_dob: "1971-11-02",
      provider_npi: "1437223344",
      diagnosis_codes: ["C78.7", "C18.7", "Z92.21"],
      procedure_codes: ["J9303", "96413"],
      clinical_notes:
        "54-year-old female with metastatic sigmoid colon adenocarcinoma with " +
        "hepatic metastases. Initial diagnosis 03/2025 after colonoscopy and liver " +
        "biopsy. Completed FOLFOX plus bevacizumab from 04/2025 through 10/2025 " +
        "with partial response followed by progression on CT 01/12/2026 demonstrating " +
        "increase in dominant liver lesion from 2.1 cm to 3.4 cm and new segment VIII " +
        "lesion. ECOG performance status 1.\n\n" +
        "Tumor is RAS wild-type and left-sided primary per pathology addendum. CEA " +
        "rose from 12.4 to 26.8 over 8 weeks. Patient reports worsening RUQ discomfort, " +
        "fatigue, and early satiety. Labs 01/18/2026: ANC 3.4, Hgb 11.9, platelets 221, " +
        "bilirubin 0.8, AST 32, ALT 28, creatinine 0.7.\n\n" +
        "Given radiographic progression after oxaliplatin-based chemotherapy, plan to " +
        "start panitumumab with irinotecan. Goals, infusion reactions, rash risk, and " +
        "electrolyte monitoring discussed. Patient wishes to proceed at outpatient cancer center.",
      insurance_id: "BCBS-TX-4472019",
      ordering_provider_name: "David Lin, MD",
      ordering_provider_npi: "1437223344",
      rendering_provider_specialty: "Medical Oncology",
      servicing_facility: "River Bend Cancer Institute Infusion Center",
      payer_name: "Blue Cross Blue Shield",
      payer_plan: "Commercial PPO",
      urgency: "urgent",
      place_of_service: "Office",
      attached_note_types: [
        "Oncology progress note",
        "Treatment history summary",
        "CT abdomen/pelvis report",
        "Pathology and biomarker report",
      ],
      prior_treatment_history: [
        "Completed FOLFOX plus bevacizumab with subsequent disease progression",
        "Supportive care optimization including antiemetics and nutrition counseling",
      ],
    },
  },
  {
    id: "orthopedic-surgery",
    title: "Outpatient Surgery Scheduling",
    specialty: "Orthopedics",
    scenario:
      "A surgery scheduler and utilization review nurse are preparing lumbar fusion documentation before reserving OR time.",
    summary:
      "Shows a musculoskeletal case where conservative treatment history, imaging findings, and functional limitation documentation drive submission quality.",
    documentationGaps: [
      "Physical therapy discharge summary and pain management notes should be attached.",
      "Confirm smoking cessation counseling and pre-op optimization plan are documented for payer review.",
    ],
    expectedFindings: [
      "Documentation Completeness Agent should flag missing conservative-treatment attachments if only summarized in the note.",
      "Clinical Evidence Retrieval Agent should capture radiculopathy, imaging-confirmed spondylolisthesis, and ADL impact.",
      "Policy Matching Agent should compare failed conservative therapy duration against lumbar fusion policy criteria.",
    ],
    nextActions: [
      "Hold surgical date until payer packet includes PT and injection documentation.",
      "Escalate to spine surgeon peer review if payer criteria remain borderline.",
    ],
    request: {
      patient_name: "Thomas Reed",
      patient_dob: "1966-08-27",
      provider_npi: "1669542008",
      diagnosis_codes: ["M43.16", "M54.16", "M48.062"],
      procedure_codes: ["22612", "22840"],
      clinical_notes:
        "59-year-old male with 14 months of refractory low back pain radiating to the " +
        "left leg in an L5 distribution with numbness and neurogenic claudication. MRI lumbar " +
        "spine 02/02/2026 shows grade 1 degenerative spondylolisthesis at L4-L5 with severe central " +
        "canal stenosis and bilateral foraminal narrowing. Dynamic X-rays demonstrate instability with " +
        "4 mm translation on flexion-extension views.\n\n" +
        "Symptoms worsen with standing more than 10 minutes or walking more than one block and interfere " +
        "with work as a warehouse supervisor. Oswestry Disability Index 46%. Conservative treatment includes " +
        "12 weeks of physical therapy, home exercise program, NSAIDs, gabapentin, two epidural steroid injections, " +
        "and activity modification with only transient improvement.\n\n" +
        "Exam: positive straight leg raise on left, dorsiflexion 4+/5, reduced sensation over lateral calf. " +
        "No bowel or bladder dysfunction. Plan is L4-L5 decompression with instrumented fusion due to instability " +
        "and failed conservative management. Risks, benefits, and alternatives reviewed with patient and spouse.",
      insurance_id: "UHC-84739122",
      ordering_provider_name: "Meghan Osei, MD",
      ordering_provider_npi: "1669542008",
      rendering_provider_specialty: "Orthopedic Spine Surgery",
      servicing_facility: "Summit Ambulatory Surgery Center",
      payer_name: "UnitedHealthcare",
      payer_plan: "Commercial HMO",
      urgency: "standard",
      place_of_service: "Ambulatory Surgery Center",
      attached_note_types: [
        "Spine surgery consult",
        "Lumbar MRI report",
        "Flexion-extension X-ray report",
        "Physical therapy summary",
      ],
      prior_treatment_history: [
        "12 weeks of physical therapy and home exercise program",
        "Two epidural steroid injections with temporary relief",
        "Medication trial with NSAIDs and gabapentin",
      ],
    },
  },
  {
    id: "dme-home-oxygen",
    title: "DME / Home Health Oxygen Setup",
    specialty: "Pulmonology / Home Care",
    scenario:
      "A discharge planner and DME coordinator are building a same-week home oxygen packet after COPD exacerbation follow-up.",
    summary:
      "Illustrates a home-based service request where test results, face-to-face documentation, and supplier routing are essential.",
    documentationGaps: [
      "Attach room-air oximetry worksheet and discharge summary if only referenced in the office note.",
      "Confirm the DME supplier order and home delivery contact details are on file.",
    ],
    expectedFindings: [
      "Documentation Completeness Agent should verify face-to-face timing and supporting oximetry evidence.",
      "Clinical Evidence Retrieval Agent should pull chronic hypoxemia findings and exacerbation history.",
      "Policy Matching Agent should assess Medicare oxygen qualification criteria and note any missing test documentation.",
    ],
    nextActions: [
      "Send completed packet to the DME supplier queue once qualifying sats are attached.",
      "If evidence is insufficient, schedule repeat walk test and resubmit the updated packet.",
    ],
    request: {
      patient_name: "Evelyn Carter",
      patient_dob: "1949-04-09",
      provider_npi: "1912084401",
      diagnosis_codes: ["J44.1", "J96.11", "R09.02"],
      procedure_codes: ["E1390", "E0431"],
      clinical_notes:
        "76-year-old female seen in pulmonary clinic 5 days after hospital discharge for COPD exacerbation. " +
        "Persistent exertional dyspnea despite Trelegy Ellipta, rescue albuterol, and prednisone taper. Resting room-air " +
        "SpO2 today 88%; decreases to 84% with ambulation after 90 feet. With 2 L/min nasal cannula, saturation improves to 93%.\n\n" +
        "Discharge summary documents acute on chronic hypoxic respiratory failure requiring supplemental oxygen. Chest X-ray without new focal infiltrate. " +
        "ABG during admission: pO2 54 mmHg on room air. Patient lives alone but daughter assists with medication setup and transportation.\n\n" +
        "Assessment: chronic hypoxemia related to COPD with need for continuous home oxygen and portable oxygen for ambulation. Face-to-face evaluation completed today; " +
        "order entered for concentrator and portable tanks. Education provided on fire safety and equipment use.",
      insurance_id: "MCR-99018821B",
      ordering_provider_name: "Janice Monroe, NP",
      ordering_provider_npi: "1912084401",
      rendering_provider_specialty: "Pulmonary Medicine",
      servicing_facility: "Harbor Pulmonary Clinic",
      payer_name: "Traditional Medicare",
      payer_plan: "Part B DME",
      urgency: "urgent",
      place_of_service: "Home",
      attached_note_types: [
        "Pulmonary follow-up note",
        "Hospital discharge summary",
        "Room-air oximetry test",
        "Home oxygen order",
      ],
      prior_treatment_history: [
        "Hospital admission for COPD exacerbation with hypoxic respiratory failure",
        "Inhaled triple therapy and steroid taper with persistent exertional hypoxemia",
      ],
    },
  },
];

export const DEFAULT_SAMPLE_CASE_ID = SAMPLE_CASES[0].id;
export const SAMPLE_REQUEST: PriorAuthRequest = SAMPLE_CASES[0].request;
