# Hensun Console Design System

## Product posture

The customer console is calm, capable, and usable by adults of different ages. Duck yellow is the recognizable product color, but the interface avoids infantile copy, oversized mascots, or game-like rewards. Public copy remains 18+ until family mode passes legal and safety review.

## Tokens

- Light: page `#FFFDF7`, surface `#FFFFFF`, text `#242218`, muted `#625E52`.
- Dark: page `#171713`, surface `#22211C`, text `#F7F4EA`.
- Brand: duck yellow `#F4C542`; yellow buttons always use dark text.
- Radius: 10 / 16 / 24 px. Touch targets are at least 44 px.
- Noto Sans SC is self-hosted from the pinned Fontsource dependency. Geist Mono is bundled through `next/font/local` for serial numbers and device identifiers. No runtime font request is made to an external host.

## Typography and hierarchy

Every page has one `h1`. Page descriptions explain the customer outcome, not implementation. Internal terms such as Provider, Bearer token, model endpoint, device key, and catalog price never appear in customer routes.

## Navigation

- Desktop: Home, Devices, Assistant, Memory, Usage, Account.
- Mobile: Home, Devices, Assistant, More. More contains memory, usage, family management, and account.
- Operations is a separate `/admin` product surface and is never linked from the customer console.

## States

Skeletons preserve final layout. Block-level failures keep the last successful data and show its timestamp. Success is placed next to the affected control. Authentication failures redirect to login with a return path. Agreement failures redirect to the agreement flow.

## Motion and accessibility

Interactive motion is limited to short translation and color transitions. `prefers-reduced-motion` disables them. Focus rings are always visible for keyboard users. Navigation uses `aria-current`; live results use `aria-live`.

## Original face source

`web/components/brand-face.tsx` is a CSS reconstruction of Hensun's company-owned idle/welcome face used by the ESP32 `HensunFaceDisplay` engine and the internal “HensunAI 60-scene expression development package V1.0”. It is used as brand identity, not sourced from the public Xiaozhi emoji set.
