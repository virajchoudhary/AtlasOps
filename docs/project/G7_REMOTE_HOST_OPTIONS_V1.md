# G7 Remote Host Options v1

**Status: non-live D1 research; no provider, region, instance, spending, model
transfer, or training is approved.** Public product terms were checked on
2026-09-30. This document is a price and control comparison, not a live
availability check or reservation.

The university-hosted A100 80 GB remains the first choice if a specific,
authorized allocation can be verified. No university account, allocation,
or host was inspected, so availability is **unverified**, not disproven.
The commercial fallback below is a recommendation for a later D1 decision.
The latest public catalogue rate is not a user-specific quote, and it does not
establish a free entitlement or available capacity.

This record supplements the [G7 pilot environment](G7_PILOT_ENVIRONMENT_V1.md)
and [G7 acceptance contract](G7_SFT_PILOT_ACCEPTANCE_V1.md). G7 remains
`PARTIAL`. D1 is unresolved; D2/D3, weight-transfer authorization, and a
run-specific launch authorization remain separate decisions. No result here
promotes an empirical gate.

## Recommendation

Preferred commercial fallback for a later approval:

| Field | Candidate |
|---|---|
| Provider and product | RunPod on-demand Pod, **Secure Cloud** only |
| GPU | **1 x NVIDIA A100 SXM, 80 GB**; the public page also lists A100 PCIe 80 GB at the same Secure Cloud rate |
| Public GPU rate | **$1.59 per GPU-hour**, observed 2026-09-30 |
| Logical resource name | `atlasops-g7-sft-pilot` |
| Region/data center | Proposed **US-KS-2 (US Kansas 2)**, a data-center identifier shown in the provider's public catalog documentation. A100/storage/encryption capacity there is unverified. No automatic region substitution; stop if this exact combination is unavailable |
| Physical host / provider ID | **Not yet assignable.** The provider assigns the actual Pod ID and machine only after creation; record both, plus the data-center code and GPU inventory, before any workload starts. Do not describe this as a named or reserved host before then |
| Access | Proposed key-only **full SSH with SCP/SFTP**, one mapped TCP port 22 and public IP; source-IP firewall restriction where supported. Basic SSH cannot transfer files with SCP/SFTP. This exposure requires explicit approval; no notebook/dashboard/inference ports |
| Storage candidate | Start with a 54 GB encrypted Volume Disk (approximately 50.3 GiB), plus a 20 GB container disk for the runtime image and temporary files. This is about 68.9 GiB total provisioned capacity, below the existing 100 GiB per-run hard ceiling; preserve at least 20 GiB of actual free working space and stop for a new decision if the measured estimate cannot fit |
| Retention | The encrypted Volume Disk survives Pod stop/restart but is tied to that Pod and is deleted when the Pod is terminated. The provider quotes a higher idle rate while stopped. A cross-Pod reload would require a separately reviewed storage/transfer choice |
| Spend authorization | **None.** The four-hour calculation below is a planning ceiling for later review, not approval to create or run a Pod |

The encrypted Volume Disk is selected over RunPod Network Storage for the
initial candidate because RunPod documents encryption at rest for Volume Disk,
but not for Network Volumes. It supports a separate local-only adapter reload
process on the same Pod. It does **not** prove that model loading, 8192-token
QLoRA memory fit, image build, adapter save, or reload will work on this GPU.
If independent reload on a newly created Pod becomes a requirement, stop and
review storage again; do not silently substitute an unencrypted network
volume.

The environment contract sets a 50 GiB initial scratch target, a 100 GiB hard
per-run ceiling, and at least 20 GiB free. The 54 GB volume is sized to meet
the initial target using RunPod's decimal-GB storage units. The container disk
is counted in the 100 GiB total here; it is not treated as free operating
system storage. The provider's final disk sizes and actual free space must be
captured from the creation summary before approval.
Here 50 GiB is **provisioned durable capacity**, not a claim of 50 GiB free
or usable after data. The 54 GB volume provides about 50.3 GiB before filesystem
overhead. Container disk is ephemeral. Reserving 20 GiB free leaves at most
48.9 GiB used across both disks; the actual complete footprint must be measured
and fit before proceeding. Do not present this proposal as measured storage fit.

### Concrete Approval Proposal

For a non-training host setup only: `atlasops-g7-sft-pilot` in Secure Cloud
US-KS-2, exactly one A100 SXM 80 GB (`NVIDIA A100-SXM4-80GB`), at least
8 vCPU/32 GiB host RAM, encrypted 54 GB Volume Disk plus 20 GB container disk,
one-hour maximum running time, and seven-day maximum stopped-volume retention.
Public-price calculation: $1.59 GPU + $0.01028 running storage + $2.52
stopped-volume retention = **$4.12028 before tax**. Proposed **$5 all-in cap**;
do not create the resource if the final checkout including CPU/RAM, tax,
account funding minimum or other required charge exceeds it.

This is a candidate cap, not a claim that checkout will fit or that a deposit
is $5. Any higher required deposit, larger image/disk requirement, different
region, or unavailable encrypted storage requires a fresh explicit decision.
Keep a later maximum-four-hour pilot/reload reservation separate:
the published estimate is $17.20 for four GPU-hours and 30 days stopped volume;
proposed $25 all-in cap, still not approval for training or actual retention.

## Cost Bound

Current public RunPod storage terms are in decimal GB per month; RunPod says
container and volume disk charges are metered per second. Using 54 GB of
encrypted Volume Disk, 20 GB of container disk, and at most four GPU-hours:

| Component | Published basis | Planning amount |
|---|---:|---:|
| One A100 SXM 80 GB, four hours | $1.59/GPU-hour | **$6.36** |
| 54 GB Volume Disk while running | $0.10/GB-month | $5.40 per normalized month; about $0.03 for four hours |
| 54 GB Volume Disk while stopped | $0.20/GB-month | **$10.80 per normalized month** |
| 20 GB container disk | $0.10/GB-month while running; not charged while stopped | $2.00 per normalized month; about $0.01 for four hours |

Assuming four GPU-hours total, the Pod runs for those four hours, and the
Volume Disk then remains stopped for one normalized 30-day month, the published
GPU and storage rates imply approximately **$17.20 before tax or other
account-specific charges**. This is a planning calculation, not a bill quote:
it prorates monthly storage at 720 hours, and does not include unverified
CPU/RAM charges, applicable taxes, a credit/deposit minimum, or any other
console line item. If the Pod or storage is retained longer, storage accrues
longer; if the model run needs more than four GPU-hours, compute exceeds this
calculation.

The four-hour figure is a proposed maximum for any later, separately approved
setup, pilot, and independent reload; it is not a measured duration or a
guarantee of completion. Do not create a savings plan, enable autopay, deposit
credits, or allocate a Pod under this document. Before any spend, the final
provider screen must show the exact GPU, region, CPU/RAM allocation, container
disk, persistent-volume size, full hourly/storage charges, tax treatment, and
any required account minimum. The user must approve an all-in USD cap that
covers those values. If the console rate or options differ from this snapshot,
recalculate and obtain fresh approval; do not use the public price as a
spending cap.

## Narrow Alternative

Lambda's public on-demand price table lists an NVIDIA A100 SXM with 80 GB
VRAM/GPU at **$2.79/GPU-hour**. The same row lists 240 vCPUs, 1,800 GiB RAM,
and 19.5 TiB SSD; it does not establish a practical one-GPU minimum resource
shape or that capacity is available to this account. Four GPU-hours at the
listed per-GPU rate would be $11.16 before tax and storage. Lambda bills
on-demand instances by hourly usage in one-minute increments, beginning after
launch health checks and ending at termination.

Lambda filesystems persist independently of an instance and continue billing
while they exist. Its public billing page gives $0.20/GiB-month only as an
**example** and explicitly warns that it may not reflect current pricing; the
current filesystem price is shown during creation. It also says filesystem
usage is billed in one-hour increments with no minimum storage period and no
ingress/egress charge. Because there is no verifiable current public
filesystem quote, and the public A100 row's CPU/RAM/instance grouping is not a
cleanly bounded match for this pilot, Lambda is a comparator, not the
recommended fallback.

## Access and Storage Controls

- Use only RunPod **Secure Cloud**, not Community Cloud. RunPod describes
  Secure Cloud as operating in T3/T4 data centers and says vetted infrastructure
  partners meet enterprise standards including SOC 2, ISO 27001, and PCI DSS.
  Those platform-level statements do not verify a selected data center,
  customer-specific contract, residency, or an individual host; review the
  location and applicable terms before D1 approval.
- Full SSH is selected because the evidence workflow needs SCP/SFTP.
  Register only the public key. Keep the private key in the local OS key
  store/agent; never print, upload or commit it. The provider public IP and
  mapped TCP 22 require explicit access approval; use source-IP restriction
  where supported and ordinary host-key checking. Do not fall back to passwords.
- Do not expose Jupyter, VS Code, HTTP, application dashboards, or TCP
  `8888`. Permit only provider-managed SSH access, using the Pod's internal SSH
  port as required by the chosen supported template. Do not configure any
  additional public TCP/HTTP ports. The custom host image's default is inert;
  only after host approval set its start command to
  `/usr/local/bin/start-sft-host`, which starts key-only SSH, not training.
- Keep all provider/model credentials out of the repository, image, logs, and
  shell history. No Hugging Face token is assumed. If a later authorized step
  demonstrates one is required, use the provider's secret facility and do not
  print it; do not place it on the persistent volume.
- Keep the synthetic Train candidate, approved model files, and run outputs
  on the encrypted Volume Disk. Do not put Validation/Test material,
  credentials, personal data, or unrelated project files there. Confirm the
  provider's encryption toggle and the exact capacity in the pre-create
  summary; if encryption is unavailable for the selected Pod/region, stop.
- Do not terminate the Pod until required artifacts have been independently
  reloaded and transferred to the approved evidence location. Termination
  deletes the Volume Disk. Any transfer destination and method require their
  own review; this document does not authorize uploading results elsewhere.

## Pre-Allocation Checklist

The candidate is not ready for a paid allocation until each item below is
confirmed in a new D1 record:

1. The user approves the proposed Secure Cloud US-KS-2 data-center
   shown by the provider, and the console currently offers one A100 SXM 80 GB
   Pod plus encrypted Volume Disk there. Current inventory is unverified.
2. The final summary states the minimum CPU/RAM and their charges. RunPod's
   public Pod pricing docs do not publish a per-Pod CPU/RAM floor or a
   standalone CPU/RAM rate for this A100 configuration, so do not infer either
   from the GPU-only catalogue price.
3. The final total includes four GPU-hours maximum, storage retention,
   CPU/RAM, tax, any credit/deposit minimum, and all other charges. The current
   credit/deposit minimum and tax amount are unverified; no account or billing
   page was accessed.
4. The disk plan remains within the 100 GiB hard per-run ceiling with at least
   20 GiB free after the actual image, package cache, model, dataset,
   checkpoint, log, and temporary-file estimate. Root has separately verified
   model-file metadata of 15,231,271,888 bytes (14.19 GiB); no weights were
   downloaded here and other storage usage remains unmeasured.
5. The user approves the exact all-in spend cap and the distinct maximum
   resource window. Host setup is limited to at most one GPU-hour if separately
   approved; the later pilot/reload maximum of four GPU-hours needs its own
   approval. A failed or interrupted attempt is preserved and is not restarted
   in-place.
6. After resource creation but before any workload starts, record the actual
   Pod ID, logical name, data-center code, GPU model/memory, CPU/RAM,
   image digest, disk sizes/encryption status, actual source SHA, and confirmed
   spend limit. If the created resource differs from the approved D1 record,
   stop without loading weights.

No account, provider inventory API, payment/billing page, or deployment console
was accessed. No host was created, no public IP/port was enabled, no credentials
or secrets were inspected, and no image, model weight, inference, training, or
GPU workload was started.

## Public Primary Sources

All public sources below were read on **2026-09-30**. Prices are USD and may
change; quotations below preserve the wording or row values supporting this
snapshot.

| Primary source | Supporting public text |
|---|---|
| [RunPod GPU pricing](https://www.runpod.io/pricing) | "On-demand A100 SXM cloud GPU rental on Runpod. Community Cloud $1.39/hr, Secure Cloud $1.59/hr." The page separately lists A100 PCIe Secure Cloud at $1.59/hr. |
| [RunPod Pod pricing and storage terms](https://docs.runpod.io/pods/pricing) | "Pods are billed by the second for compute and storage, with no fees for data ingress or egress." Its storage table lists Volume Disk as `$0.10/GB/month` running and `$0.20/GB/month` stopped, and says stopped-Pod storage continues to accrue. |
| [RunPod storage options](https://docs.runpod.io/pods/storage/types) | "Volume disk" is retained through the Pod lease and survives stop/restart; encrypted Volume Disk is encrypted at rest and accessible only to that Pod. The same page says container disk is lost on stop and container/network volumes cannot be encrypted. |
| [RunPod SSH setup](https://docs.runpod.io/pods/configuration/use-ssh) | The comparison labels Basic SSH as requiring no public IP and an SSH key; full SSH uses a public IP and public TCP port 22. |
| [RunPod public catalog schema](https://docs.runpod.io/api-reference-v2/catalog/list-data-centers.md) | Documentation response example names `US-KS-2`, `US Kansas 2`, `NORTH_AMERICA`. This is not a live GPU inventory response. |
| [RunPod GPU IDs](https://docs.runpod.io/references/gpu-types.md) | `NVIDIA A100-SXM4-80GB` is the catalog ID for A100 SXM 80 GB. |
| [RunPod custom templates](https://docs.runpod.io/pods/templates/create-custom-template) | Custom images need an appropriate startup command to remain accessible; full SSH needs a running SSH daemon. No model preloading or tutorial inference step is authorized here. |
| [RunPod security and compliance](https://docs.runpod.io/references/security-and-compliance) | RunPod describes Secure Cloud as T3/T4 data centers and vetted partners meeting enterprise standards including SOC 2, ISO 27001, and PCI DSS; this is not host-specific verification. |
| [Lambda GPU pricing](https://lambda.ai/pricing#instances) | The public A100 SXM row reads `80 GB` VRAM/GPU, `240` vCPUs, `1800 GiB` RAM, `19.5 TiB SSD`, `$2.79`/GPU/hour. |
| [Lambda billing overview](https://docs.lambda.ai/public-cloud/billing/) | On-demand instances bill hourly usage in one-minute increments. The filesystem example `$0.20/GiB/month` is explicitly illustrative, not current pricing; the current rate is shown at filesystem creation. Billing continues while a filesystem exists, with no minimum storage period and no ingress/egress charge. |
