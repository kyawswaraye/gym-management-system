# ADR 0011: Bare React Native member application

- **Status:** Accepted
- **Decision date:** 2026-10-20
- **Applies from:** Phase 7 architecture

## Context

`docs/DEVELOPMENT_PLAN.md` decision 9 left Expo versus bare React Native open until Phase 7. The member session in [ADR 0005](0005-member-session-foundation.md) already publishes login, refresh, and logout for one pre-linked active member at one active gym. Public linking, multi-gym member selection, and a member MFA challenge do not exist. Check-in scan in [ADR 0006](0006-member-check-in-scan.md) is already published for that same principal. The mobile client still has to store a refresh credential, scan an onsite QR, and ship to Android and iOS without putting member tokens through the browser BFF.

This record chooses the client stack, the supported toolchain and devices, secure storage, privacy, accessibility, and release signing. It does not create the `/mobile` workspace. Push delivery is [ADR 0012](0012-fcm-push-delivery.md).

## Decision

### Client stack

The member application is a bare React Native CLI app with TypeScript in `/mobile`. It targets Android and iOS from one codebase. Expo, Expo Go, and Expo Application Services are not used. The app calls Django `/api/v1/member-auth/` and `/api/v1/member/` directly with a bearer member access token. It does not send `X-Gym-BFF-Secret` and does not use the staff audience.

The scaffold, when it is approved to start, uses the React Native CLI stable release current on that day, the New Architecture, and Hermes. It records the exact React Native, React, and native-module versions in `mobile/package.json`. This record does not invent those version numbers before the scaffold exists. Node.js stays on the repository pin, 24.20.0. Android builds use JDK 17. iOS builds run on a macOS runner whose Xcode supports the deployment target below.

Approved libraries, and no others until a later decision, are React Navigation, TanStack Query, React Hook Form, Zod, `react-native-keychain`, `react-native-vision-camera`, and React Native Firebase app plus messaging. Firebase files and signing material stay out of git.

### Supported devices

Supported phones are Android 8.0 (API 26) and newer, and iOS 16.0 and newer. The Android `minSdk` is 26. The iOS deployment target is 16.0. Tablets and foldables use the phone layout. A separate tablet design is not part of this decision. Simulators and emulators are for tests. Release evidence still needs one physical Android phone and one physical iPhone on the minimum supported major version.

### Secure storage

The refresh token is stored only through `react-native-keychain`, which uses the iOS Keychain and the Android Keystore. The item is device-local: iOS `WHEN_UNLOCKED_THIS_DEVICE_ONLY`, and Android StrongBox when the device has it, otherwise the hardware-backed Keystore. A device biometric may gate retrieval of that item through the platform API. The app does not collect, transmit, or store a biometric template, embedding, or image.

The access token stays in process memory. It is not written to the Keychain, AsyncStorage, `localStorage`, logs, or a crash report. The app-install identifier used for device registration is a random UUID created on first launch and stored in the same Keychain item family. Logout deletes the Keychain items, drops the in-memory access token, and clears the query cache. A refresh failure that means the session is revoked does the same cleanup.

### Privacy

The app is member-self only. It has no gym picker and no account-linking screen. Login failure stays the generic credential error from ADR 0005, including zero links, several links, and an MFA-enabled user. The app must not offer a way to choose `member_id` or `gym_id`.

The camera is used for the onsite check-in QR and, when the member chooses, for a profile photo. The system photo picker may supply a profile photo without a broad photo-library permission. QR payloads are scanned and sent as the existing `payload` field. They are not logged, not stored as a history of secrets, and not placed in analytics. There is no analytics SDK and no advertising SDK in this decision.

Android auth screens set `FLAG_SECURE`. iOS cannot fully block screenshots, so auth screens do not render the refresh token, the access token, or a reset token. Crash and diagnostic reports exclude tokens, push tokens, presigned URLs, and profile-photo bytes.

### Accessibility

The target is WCAG 2.2 AA for the member flows: every control has an accessible name, text and controls meet the contrast ratio, touch targets are at least 44 by 44 points, and the primary actions remain reachable at 200 percent text size. Respect the platform reduce-motion setting. A denied camera permission still explains how check-in works at the staffed desk. A denied notification permission still leaves the in-app inbox usable.

### Release and signing

Android release builds use Play App Signing. The upload key is a CI secret. The debug keystore stays on the developer machine. iOS release builds use an Apple Distribution certificate and a provisioning profile, both CI secrets. Version name comes from the mobile package. Android `versionCode` and iOS `CFBundleVersion` increase by one for each store upload.

`google-services.json`, `GoogleService-Info.plist`, keystores, `.p8` keys, and provisioning profiles are gitignored. Release builds do not enable cleartext HTTP. Debug builds may allow cleartext only to the local API host. iOS App Transport Security stays on in release. A debug exception for local HTTP is removed from the release plist.

Store listing, privacy nutrition labels, and the camera and notification permission strings must match what the app actually does: member login, profile, schedules, QR check-in, and optional push. This record does not submit the app to either store.

## Security consequences

A stolen unlocked device can use the Keychain item until logout, revocation, or biometric lock if the member turned that gate on. The access token's short life limits a memory-only copy. The BFF secret never ships inside the app, so a reversed binary does not become a browser-login credential. Biometric unlock protects local retrieval only. It is not member MFA and it does not satisfy staff step-up.

Public linking, a multi-gym member picker, and a member MFA challenge stay closed. An MFA-enabled user still cannot complete member login. A user linked to two gyms still receives the same generic failure as a wrong password.

## Compatibility consequences

`docs/DEVELOPMENT_PLAN.md` stays the historical baseline and is not edited. Decision 9 remains the historical open item. This record resolves the client choice for every current document other than that baseline. [ADR 0005](0005-member-session-foundation.md) login, refresh, and logout stay as published. No `/mobile` tree, native project, or store binary is created by this record. The later implementation publishes those HTTP and data contracts in the `/mobile` workspace and API. Live Firebase and APNs credentials, release signing, store submission, and a production device proof remain open. Production deployment is not complete.

## Alternatives considered

### Expo managed or Expo prebuild

Rejected. The program needs owned Android and iOS projects, direct FCM and camera native modules, and CI signing without Expo's cloud build as a required host.

### Wrap the member session in the Next.js BFF

Rejected. ADR 0002 keeps the BFF secret and browser cookies on the staff principal. Member tokens are already defined as a direct JSON-body contract.

### Require member MFA in this decision

Rejected. ADR 0005 fails closed when MFA is enabled. A member challenge is a separate contract and stays deferred.
