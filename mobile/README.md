# Gym Member

Bare React Native 0.88 CLI app for Android and iOS. It calls the Django member API with a bearer member access token. It does not use Expo and it does not send `X-Gym-BFF-Secret`.

Supported devices are Android 8.0 (API 26) and newer, and iOS 16.0 and newer. The JavaScript engine is Hermes and the New Architecture is enabled.

## Requirements

- Node.js 24.20.0
- TypeScript 6.0.3 (the React Native ESLint parser loads the compiler through CommonJS; TypeScript 7 does not expose that API)
- JDK 17
- Android SDK platform `android-37.0`, build-tools `37.0.0`, and NDK `27.1.12297006` for a debug build. `minSdk` is 26.
- Xcode that can deploy to iOS 16, plus CocoaPods, for an iOS build
- Ruby 4.0.1, recorded in `.ruby-version`. The macOS system Ruby 2.6.10 cannot install current CocoaPods. Bundler 4.0.3 installs the locked CocoaPods 1.15.2. Ruby 3.4 and newer also need the `base64` gem, which is in the Gemfile.

From the repository root, `scripts/check-node.sh` accepts any Node 24. This package pins `24.20.0`.

## Setup

```bash
cd mobile
npm ci
cp .env.example .env
npm run env:sync
```

`.env` is local. `npm run env:sync` rewrites `src/config/env.local.ts`. Debug HTTP is accepted only for `10.0.2.2`, `localhost`, and `127.0.0.1`. A release bundle must use `APP_ENV=release` and an `https://` origin.

Android debug cleartext is allowed only for `10.0.2.2`, which is the emulator alias for the host machine. iOS debug App Transport Security allows insecure HTTP only for `localhost`. Release builds use HTTPS on both platforms.

The debug keystore is created by Gradle when it is missing. It is not committed. Release signing reads `ANDROID_UPLOAD_STORE_FILE`, `ANDROID_UPLOAD_STORE_PASSWORD`, `ANDROID_UPLOAD_KEY_ALIAS`, and `ANDROID_UPLOAD_KEY_PASSWORD`. Those values stay outside the repository.

## Run

Start Metro:

```bash
npm start
```

Android emulator, from `mobile`:

```bash
npm run android
```

iOS simulator, after pods. `bundle install` uses the Gemfile in this directory and Ruby 4.0.1. Then install pods from `ios`:

```bash
bundle install
cd ios
bundle exec pod install
cd ..
npm run ios
```

## Checks

From the repository root, with Node.js 24.20.0 on `PATH`:

```bash
make mobile-check
make mobile-test
make mobile-android
make mobile-ios
```

`make mobile-check` runs format, lint, and typecheck. `make mobile-test` runs Jest and the member-journey test. `make mobile-android` runs `./gradlew assembleDebug`. `make mobile-ios` installs the locked CocoaPods gems, runs `pod install`, and builds the arm64 iOS Simulator app with signing disabled.

The same commands from `mobile`:

```bash
npm run format:check
npm run lint
npm run typecheck
npm test
npm run test:e2e
```

`npm test` is Jest and React Native Testing Library. `npm run test:e2e` walks login, profile, class booking idempotency, and logout against a fake HTTP server. It does not install a simulator or a device runner.

CI runs `npm audit --audit-level=critical` in the mobile job. The 2026-10-21
audit reported 0 critical, 49 high, and 5 moderate findings (54 total).
`npm audit --omit=dev` still reported 20 high findings and 0 critical. Both
commands exit non-zero because findings exist; the CI threshold is critical,
so these high and moderate results do not fail the job.

Both root advisories are transitive, and npm publishes no compatible patched
release that keeps React Native 0.88:

- High [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)
  (`braces` `<=3.0.3`, CWE-674). The installed version is `3.0.3`, which is
  also the newest 3.x release. `micromatch` pulls it into Jest, Metro
  `0.87.1` via `metro-file-map`, and `@react-native-community/cli` `20.2.0`
  via `fast-glob`. Application source does not import `braces`. The issue is
  stack exhaustion when a tool expands a deeply nested glob. That is a
  developer and CI bundler input, not a member API or on-device parser. The
  shipped app does not become a production push or QR endpoint for this
  advisory.
- Moderate [GHSA-hp3w-g68c-fv3c](https://github.com/advisories/GHSA-hp3w-g68c-fv3c)
  (`sprintf-js` `<=1.1.3`, CWE-1284). The installed `1.0.3` is only under
  Jest's Istanbul `argparse` chain. It is absent from `npm audit --omit=dev`.

npm's suggested fixes are semver-major downgrades, including React Native
`0.72.17`, the CLI packages `13.5.1` / `15.1.0` / `15.0.0`, and Jest `30.5.2`.
Those were not applied. The Firebase gRPC override remains
`@grpc/grpc-js` `1.14.6`, and that package was not in this audit. Reassess
both advisories on each React Native toolchain update.

Android debug compile:

```bash
cd android
./gradlew assembleDebug
```

iOS simulator build, without signing:

```bash
bundle install
cd ios
bundle exec pod install
xcodebuild \
  -workspace GymMember.xcworkspace \
  -scheme GymMember \
  -configuration Debug \
  -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' \
  ARCHS=arm64 \
  ONLY_ACTIVE_ARCH=YES \
  CODE_SIGNING_ALLOWED=NO \
  build
```

## Firebase Cloud Messaging and APNs

Push is optional. The inbox works when notification permission is denied.

1. Create a Firebase project and an Android app with package `com.gymplatform.member`.
2. Download `google-services.json` and place it at `android/app/google-services.json`. Gradle applies the Google services plugin only when that file exists. `firebase/google-services.json.example` shows the shape with placeholder values.
3. Create an iOS app with bundle id `com.gymplatform.member`. Download `GoogleService-Info.plist` and place it at `ios/GymMember/GoogleService-Info.plist`. Add that file to the GymMember target in Xcode. `firebase/GoogleService-Info.plist.example` is the placeholder.
4. In the Apple developer account, create an APNs authentication key (`.p8`). Upload that key, its key id, and the team id in the Firebase project. Do not commit the `.p8`, the key id, or the team id.
5. The iOS target has the Push Notifications entitlement. Debug uses `aps-environment` `development`. Release uses `production`.
6. Turn push on in the app's notification settings. The app registers the FCM token only after that consent. Turning it off clears the token on the server. Sign-out deletes this installation's device row.

`google-services.json`, `GoogleService-Info.plist`, keystores, `.p8` keys, and provisioning profiles are gitignored.

## Session storage

The access token stays in memory. The refresh token and the installation id use `react-native-keychain` with `WHEN_UNLOCKED_THIS_DEVICE_ONLY`. Android tries the hardware-backed keystore first. Sign-out calls logout, then deletes those items and clears the query cache. A revoked refresh session clears the same local state.

The camera scans onsite check-in QR codes and can take a profile photo. QR payloads, tokens, and presigned photo URLs are not written to logs.

## API types

`src/types/api.generated.ts` is generated from `contracts/openapi.v1.json`:

```bash
npm --prefix ../contracts/generator ci
npm --prefix ../contracts/generator run generate
```

That command also refreshes the web types.
