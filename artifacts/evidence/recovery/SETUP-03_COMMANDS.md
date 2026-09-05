# SETUP-03 command record

Exact PowerShell command strings issued by the root through the pre-commit evidence checks, in order. All invocations used `login: false` in `C:\AtlasOps`. This record excludes the preceding read-only recovery-audit task. Final log staging, commit, and independent read-only review commands are reported with the final task result.

Commands 5 and 8 were run with the narrowly authorized Git sandbox escalation. The combined move/restore command moved ten files successfully, then its Git restore failed to create `.git/index.lock`; the next command retried only that authorized restore successfully. The first post-move verification command had a PowerShell empty-pipe parser error and executed no checks; the following corrected command passed. `git add` used the same narrow Git escalation.

Non-shell edits used `apply_patch` to create the recovery JSON before moving/restoring/ignoring files; add the Stage 4 index, mock archive README and scoped Git attributes; append the narrow root uv.lock ignore; and create this command record. The final JSON/hash update was executed by the recorded PowerShell command. No raw experiment artifact was edited through `apply_patch`.

## 1

```powershell
Get-Content -LiteralPath 'C:\Users\viraj\.codex\attachments\387652ff-3b60-4d1a-89d1-e3c624fd5c4a\pasted-text.txt'
```

## 2

```powershell
Get-Content AGENTS.md; git --no-optional-locks status --porcelain=v1 --untracked-files=all; git --no-optional-locks branch --show-current; git --no-optional-locks rev-parse HEAD; git --no-optional-locks diff -- artifacts/evidence/stage10/rs_dataset_manifest.json; Get-Content .gitignore; rg -n 'stage6|stage8|stage9|uv.lock|mock_archive' tests bench scripts artifacts/SUBMISSION_MANIFEST.json requirements/README.md .gitignore; rg --files -g AGENTS.md artifacts
```

## 3

```powershell
git --no-optional-locks grep -n -E 'artifacts/evidence/stage(6|8|9)|zero_shot_(val|test)_summary|sft_(val|test)_(summary|episodes)|grpo_(val|test)_(summary|episodes)|uv\.lock' -- ':!*.lock'; Get-Content tests/test_stage6_zero_shot_baseline.py; Get-Content tests/test_stage8_sft_eval.py; Get-Content tests/test_stage9_grpo_pipeline.py; Get-Content tests/test_stage15_submission_package.py; Get-Content scripts/package_submission.py; Get-Content requirements/README.md; git --no-optional-locks check-attr -a -- artifacts/evidence/stage4/EXP-STAGE4-SF002-010.json artifacts/evidence/stage6/zero_shot_val_summary.json; git --no-optional-locks config --get core.autocrlf; git --no-optional-locks branch --list chore/evidence-reconciliation
```

## 4

```powershell
$ErrorActionPreference='Stop'; $status=@(git --no-optional-locks status --porcelain=v1 --untracked-files=all); if ($LASTEXITCODE -ne 0) { throw 'Git status failed' }; $paths=@($status | ForEach-Object { $_.Substring(3) }); $snapshot=@($paths | ForEach-Object { $item=Get-Item -LiteralPath $_; [ordered]@{path=$_;sha256=(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant();size_bytes=$item.Length} }); [ordered]@{timestamp=[DateTime]::UtcNow.ToString('o');source_branch=(git --no-optional-locks branch --show-current);source_head=(git --no-optional-locks rev-parse HEAD);initial_status=$status;files=$snapshot;stage10_diff=(@(git --no-optional-locks diff -- artifacts/evidence/stage10/rs_dataset_manifest.json) -join "`n");stage10_head_blob=(git --no-optional-locks rev-parse HEAD:artifacts/evidence/stage10/rs_dataset_manifest.json)} | ConvertTo-Json -Depth 6
```

## 5

```powershell
git switch -c chore/evidence-reconciliation
```

## 6

```powershell
$ErrorActionPreference='Stop'; $p=Get-Content -Raw artifacts/evidence/recovery/2026-09-05-workspace-recovery.json | ConvertFrom-Json; if ((git --no-optional-locks branch --show-current) -ne $p.recovery_branch -or (git --no-optional-locks rev-parse HEAD) -ne $p.source_head) { throw 'Branch or HEAD changed' }; foreach($f in $p.files){$item=Get-Item -LiteralPath $f.original_path; if($item.Length -ne $f.original_size_bytes -or (Get-FileHash -LiteralPath $f.original_path -Algorithm SHA256).Hash -ne $f.original_sha256){throw ('Snapshot mismatch: '+$f.original_path)}}; $old=(git --no-optional-locks show HEAD:artifacts/evidence/stage10/rs_dataset_manifest.json) -join "`n"; $oldObj=$old | ConvertFrom-Json; $newObj=Get-Content -Raw artifacts/evidence/stage10/rs_dataset_manifest.json | ConvertFrom-Json; if($oldObj.file.Replace('/pytest-525/','/pytest-536/') -ne $newObj.file -or $oldObj.file -eq $newObj.file){throw 'Unexpected manifest path diff'}; $oldObj.PSObject.Properties.Remove('file'); $newObj.PSObject.Properties.Remove('file'); if(($oldObj|ConvertTo-Json -Depth 20 -Compress) -ne ($newObj|ConvertTo-Json -Depth 20 -Compress)){throw 'Manifest fields changed beyond file'}; if((@(git --no-optional-locks diff -- artifacts/evidence/stage10/rs_dataset_manifest.json) -join "`n") -ne $p.stage10_restore.exact_diff){throw 'Diff changed'}; foreach($f in $p.files | Where-Object classification -ne 'UNAPPROVED_LOCAL_DEPENDENCY_RESOLUTION'){$raw=[IO.File]::ReadAllText((Join-Path 'C:\AtlasOps' $f.original_path)); if($raw -match '(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}|\bsk-[A-Za-z0-9_-]{30,}|\bAIza[0-9A-Za-z_-]{30,}'){throw ('Credential-shaped material requires review in '+$f.original_path)}}; if(Test-Path artifacts/evidence/.gitattributes){throw 'Existing evidence attributes require inspection'}; 'Pre-mutation validation PASS: 26 SHA/size matches; Stage 10 path-only diff confirmed; no credential-shaped matches in proposed evidence files.'
```

## 7

```powershell
$ErrorActionPreference='Stop'; $rootPath=(Resolve-Path -LiteralPath 'C:\AtlasOps').Path.TrimEnd('\')+'\'; $p=Get-Content -Raw artifacts/evidence/recovery/2026-09-05-workspace-recovery.json | ConvertFrom-Json; $moves=@($p.files | Where-Object classification -eq 'MOCK_DETERMINISTIC_OUTPUT'); foreach($f in $moves){$src=[IO.Path]::GetFullPath((Join-Path $rootPath $f.original_path)); $dst=[IO.Path]::GetFullPath((Join-Path $rootPath $f.final_path)); if(-not $src.StartsWith($rootPath,[StringComparison]::OrdinalIgnoreCase) -or -not $dst.StartsWith($rootPath,[StringComparison]::OrdinalIgnoreCase) -or $f.final_path -notmatch '^artifacts/evidence/mock_archive/stage[689]/[^/]+$'){throw 'Move outside approved paths'}; if(Test-Path -LiteralPath $dst){throw ('Destination exists: '+$f.final_path)}; if((Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash -ne $f.original_sha256){throw ('Source changed: '+$f.original_path)}}; foreach($f in $moves){$src=Join-Path $rootPath $f.original_path; $dst=Join-Path $rootPath $f.final_path; New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null; Move-Item -LiteralPath $src -Destination $dst; if((Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash -ne $f.original_sha256){throw ('Moved bytes changed: '+$f.final_path)}}; $manifest='artifacts/evidence/stage10/rs_dataset_manifest.json'; $record=$p.files | Where-Object original_path -eq $manifest; if((Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash -ne $record.original_sha256){throw 'Manifest changed before restore'}; git --no-optional-locks restore --source=HEAD --worktree -- $manifest; if($LASTEXITCODE -ne 0){throw 'Manifest restoration failed'}; 'Moved 10 mock files with matching SHA-256; restored only Stage 10 manifest from HEAD.'
```

## 8

```powershell
git --no-optional-locks restore --source=HEAD --worktree -- artifacts/evidence/stage10/rs_dataset_manifest.json
```

## 9

```powershell
$ErrorActionPreference='Stop'; $p=Get-Content -Raw artifacts/evidence/recovery/2026-09-05-workspace-recovery.json | ConvertFrom-Json; foreach($f in $p.files){$item=Get-Item -LiteralPath $f.final_path; $sha=(Get-FileHash -LiteralPath $f.final_path -Algorithm SHA256).Hash.ToLowerInvariant(); if($f.classification -ne 'TEST_TEMPORARY_PATH_CONTAMINATION' -and ($sha -ne $f.original_sha256 -or $item.Length -ne $f.original_size_bytes)){throw ('Preservation failed: '+$f.final_path)}; if($f.classification -eq 'MOCK_DETERMINISTIC_OUTPUT' -and (Test-Path -LiteralPath $f.original_path)){throw ('Original mock path still exists: '+$f.original_path)}; [pscustomobject]@{original_path=$f.original_path;final_path=$f.final_path;final_sha256=$sha;final_size_bytes=$item.Length}} | ConvertTo-Json -Depth 4; git --no-optional-locks diff --exit-code HEAD -- artifacts/evidence/stage10/rs_dataset_manifest.json; if($LASTEXITCODE -ne 0){throw 'Stage 10 differs from HEAD'}; git --no-optional-locks check-ignore -v uv.lock; if($LASTEXITCODE -ne 0){throw 'uv.lock not ignored'}; if(@(git --no-optional-locks ls-files -- uv.lock).Count -ne 0){throw 'uv.lock is tracked'}; git --no-optional-locks diff --check; if($LASTEXITCODE -ne 0){throw 'Whitespace check failed'}; git --no-optional-locks diff --stat; git --no-optional-locks status --short --branch --untracked-files=all
```

## 10

```powershell
$ErrorActionPreference='Stop'; $p=Get-Content -Raw artifacts/evidence/recovery/2026-09-05-workspace-recovery.json | ConvertFrom-Json; $results=@(foreach($f in $p.files){$item=Get-Item -LiteralPath $f.final_path; $sha=(Get-FileHash -LiteralPath $f.final_path -Algorithm SHA256).Hash.ToLowerInvariant(); if($f.classification -ne 'TEST_TEMPORARY_PATH_CONTAMINATION' -and ($sha -ne $f.original_sha256 -or $item.Length -ne $f.original_size_bytes)){throw ('Preservation failed: '+$f.final_path)}; if($f.classification -eq 'MOCK_DETERMINISTIC_OUTPUT' -and (Test-Path -LiteralPath $f.original_path)){throw ('Original mock path still exists: '+$f.original_path)}; [pscustomobject]@{original_path=$f.original_path;final_path=$f.final_path;final_sha256=$sha;final_size_bytes=$item.Length}}); $results | ConvertTo-Json -Depth 4; git --no-optional-locks diff --exit-code HEAD -- artifacts/evidence/stage10/rs_dataset_manifest.json; if($LASTEXITCODE -ne 0){throw 'Stage 10 differs from HEAD'}; git --no-optional-locks check-ignore -v uv.lock; if($LASTEXITCODE -ne 0){throw 'uv.lock not ignored'}; if(@(git --no-optional-locks ls-files -- uv.lock).Count -ne 0){throw 'uv.lock is tracked'}; git --no-optional-locks diff --check; if($LASTEXITCODE -ne 0){throw 'Whitespace check failed'}; git --no-optional-locks diff --stat; git --no-optional-locks status --short --branch --untracked-files=all
```

## 11

```powershell
$ErrorActionPreference='Stop'; $path='C:\AtlasOps\artifacts\evidence\recovery\2026-09-05-workspace-recovery.json'; $p=Get-Content -LiteralPath $path -Raw | ConvertFrom-Json; foreach($f in $p.files){$item=Get-Item -LiteralPath $f.final_path; $sha=(Get-FileHash -LiteralPath $f.final_path -Algorithm SHA256).Hash.ToLowerInvariant(); if($f.classification -ne 'TEST_TEMPORARY_PATH_CONTAMINATION' -and ($sha -ne $f.original_sha256 -or $item.Length -ne $f.original_size_bytes)){throw ('Preservation mismatch: '+$f.final_path)}; $f.final_sha256=$sha; $f.final_size_bytes=$item.Length}; $p.verification_status='worktree_preservation_verified'; $p | Add-Member -NotePropertyName verification -NotePropertyValue ([ordered]@{verified_at=[DateTime]::UtcNow.ToString('o');stage4_raw_files_sha256_and_size_matched=14;mock_raw_files_sha256_and_size_matched=10;old_mock_paths_absent=$true;stage10_matches_source_head=$true;uv_lock_original_bytes_retained_locally=$true;runtime_code_changed=$false;experimental_outputs_regenerated=$false;infrastructure_operations_performed=$false;tests_run='No application tests; read-only hash, path, diff, and ignore checks only. Staged/committed blob checks and independent review follow.'}); [IO.File]::WriteAllText($path,($p | ConvertTo-Json -Depth 20)+"`n",[Text.UTF8Encoding]::new($false)); 'Recovery provenance finalized with original and final hashes for all 26 files.'
```

## 12

```powershell
git add -- '.gitignore' 'artifacts/evidence/stage4/RECOVERY_INDEX_009_014.md' 'artifacts/evidence/mock_archive/README.md' 'artifacts/evidence/.gitattributes' 'artifacts/evidence/recovery/2026-09-05-workspace-recovery.json' 'artifacts/evidence/stage4/EXP-STAGE4-SF002-009.cleanup.json' 'artifacts/evidence/stage4/EXP-STAGE4-SF002-009.interruption.json' 'artifacts/evidence/stage4/EXP-STAGE4-SF002-009.leftover-chaos.yaml' 'artifacts/evidence/stage4/EXP-STAGE4-SF002-010.cleanup.json' 'artifacts/evidence/stage4/EXP-STAGE4-SF002-010.json' 'artifacts/evidence/stage4/EXP-STAGE4-SF002-011.cleanup.json' 'artifacts/evidence/stage4/EXP-STAGE4-SF002-011.interruption.json' 'artifacts/evidence/stage4/EXP-STAGE4-SF002-011.leftover-chaos.yaml' 'artifacts/evidence/stage4/EXP-STAGE4-SF002-012.cleanup.json' 'artifacts/evidence/stage4/EXP-STAGE4-SF002-012.interruption.json' 'artifacts/evidence/stage4/EXP-STAGE4-SF002-013.cleanup.json' 'artifacts/evidence/stage4/EXP-STAGE4-SF002-013.interruption.json' 'artifacts/evidence/stage4/EXP-STAGE4-SF002-014.cleanup.json' 'artifacts/evidence/stage4/EXP-STAGE4-SF002-014.interruption.json' 'artifacts/evidence/mock_archive/stage6/zero_shot_test_summary.json' 'artifacts/evidence/mock_archive/stage6/zero_shot_val_summary.json' 'artifacts/evidence/mock_archive/stage8/sft_test_episodes.jsonl' 'artifacts/evidence/mock_archive/stage8/sft_test_summary.json' 'artifacts/evidence/mock_archive/stage8/sft_val_episodes.jsonl' 'artifacts/evidence/mock_archive/stage8/sft_val_summary.json' 'artifacts/evidence/mock_archive/stage9/grpo_test_episodes.jsonl' 'artifacts/evidence/mock_archive/stage9/grpo_test_summary.json' 'artifacts/evidence/mock_archive/stage9/grpo_val_episodes.jsonl' 'artifacts/evidence/mock_archive/stage9/grpo_val_summary.json'
```

## 13

```powershell
$ErrorActionPreference='Stop'; $p=Get-Content -Raw artifacts/evidence/recovery/2026-09-05-workspace-recovery.json | ConvertFrom-Json; $raw=@($p.files | Where-Object { $_.classification -eq 'MOCK_DETERMINISTIC_OUTPUT' -or $_.original_path.StartsWith('artifacts/evidence/stage4/') }); foreach($f in $raw){if((Get-FileHash -LiteralPath $f.final_path -Algorithm SHA256).Hash -ne $f.original_sha256){throw ('Raw SHA mismatch: '+$f.final_path)}; $rawBlob=git --no-optional-locks hash-object --no-filters -- $f.final_path; $stagedBlob=git --no-optional-locks rev-parse (':'+$f.final_path); if($rawBlob -ne $stagedBlob){throw ('Index normalized raw evidence: '+$f.final_path)}; if($f.final_path.EndsWith('.json')){Get-Content -Raw -LiteralPath $f.final_path | ConvertFrom-Json | Out-Null}; if($f.final_path.EndsWith('.jsonl')){Get-Content -LiteralPath $f.final_path | ForEach-Object { $_ | ConvertFrom-Json | Out-Null }}}; $authored=@('.gitignore','artifacts/evidence/.gitattributes','artifacts/evidence/mock_archive/README.md','artifacts/evidence/recovery/2026-09-05-workspace-recovery.json','artifacts/evidence/stage4/RECOVERY_INDEX_009_014.md'); $expected=@($authored)+@($raw.final_path); $actual=@(git --no-optional-locks diff --cached --name-only); if(@(Compare-Object ($expected|Sort-Object) ($actual|Sort-Object)).Count){throw 'Staged scope mismatch'}; if(@(git --no-optional-locks diff --name-only).Count){throw 'Unexpected unstaged changes'}; git --no-optional-locks diff --cached --check -- $authored; if($LASTEXITCODE -ne 0){throw 'Authored whitespace check failed'}; git --no-optional-locks diff --exit-code HEAD -- artifacts/evidence/stage10/rs_dataset_manifest.json; if($LASTEXITCODE -ne 0){throw 'Stage10 not restored'}; if((git --no-optional-locks rev-parse main) -ne $p.source_head){throw 'Main moved'}; 'PASS: 24 raw SHA-256 and staged blob identities; JSON/JSONL parse checks; exact 29-path staged scope; no unstaged changes; authored whitespace; restored manifest; main unchanged.'; git --no-optional-locks diff --cached --stat; git --no-optional-locks config --get core.hooksPath; Get-ChildItem -LiteralPath .git/hooks -File | Select-Object Name,Length
```
