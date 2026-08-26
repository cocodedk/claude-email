# Device facts — captured 2026-08-24 while physically attached

Captured during a limited connected window so the device-dependent design can proceed
offline. Source: `adb` against the attached handset.

| Fact | Value | Why it matters |
|---|---|---|
| Model / brand | SM-A528B (Galaxy A52s 5G) / samsung | the enrollment target |
| Android / SDK | 14 / 34 | app minSdk is 24, so well inside range |
| Serial | R5CR925CZAV | the ceremony requires an explicitly selected serial |
| Transport | `usb:5-1.1` | real USB, not a `host:port` endpoint — the §12 L261 transport check passes |
| `ro.kernel.qemu` | `0` | not an emulator — the §12 L262 check passes |
| Generic fingerprint | no | corroborates: physical hardware |
| `android.hardware.hardware_keystore` | **4** | TEE-backed KeyMint v4 present |
| `android.hardware.strongbox_keystore` | **ABSENT** | **no StrongBox on this device** |
| `android.software.secure_lock_screen` | present | a screen lock can gate key use |
| App installed | **NO** (`com.cocode.claudeemailapp` not present) | blocks the APK cert-pin capture |

## What this settles

**StrongBox is not available on the target device.** §7.2 L175 specifies "non-exportable
Keystore preferred, StrongBox preferred-not-required" — that hedge was correct and is now
confirmed empirically. Any design that *required* StrongBox would be dead on this handset.
The available guarantee is a TEE-backed (KeyMint v4) non-exportable key, which is what the
S1 spike must target.

## What is still blocked, and why the emulator cannot substitute

- **S1 (OpenPGP signing over a non-exportable Keystore key)** still needs this physical
  device. An Android emulator implements Keymaster in **software**, so it can prove the API
  plumbing works but proves *nothing* about non-exportability or TEE residency — which is the
  entire point of S1. Running S1 on an emulator and calling it passed would be a false green.
- **APK signing-certificate digest (§12 L265, the pinned trust anchor)** cannot be captured:
  the app is not installed. Capturing it needs a **release** build installed on the device
  (`KEYSTORE_PATH` and friends, per `app/build.gradle.kts:45-55`). Until then p3-cer-2-apk
  has no digest to pin. Note §12 L265's own warning: "CI secret setup is not a trust source;
  the pinned digest is."

## Where the emulator IS the right tool

It is the **negative** test case the physical device cannot provide: the ceremony must
*reject* emulators (§12 L262) and network transports (§12 L261). An emulator serial is
`emulator-5554` and reports `ro.kernel.qemu=1`, so it exercises both rejection paths for
real. Also fine on an emulator: app unit and instrumentation tests, the provisioning UI,
PGP-library integration, envelope/fixture work, and the encrypt/decrypt logic from
Amendment E2E — everything except the hardware guarantee.
verified boot: green  fingerprint: samsung/a52sxqeea/a52sxq:14/UP1A.231005.007/A528BXXSBGYI3:user/release-keys
