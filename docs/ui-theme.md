# Aurora dashboard design

The existing operations dashboard now uses a charcoal, burgundy, coral and lavender identity. This is an original design inspired by the supplied references, not a copy of their source components.

## Reference direction

- https://auragradients.vercel.app/?gradient=blood-aurora — the named Blood Aurora direction informed the warm aurora treatment. The reference could not be retrieved, so its exact color values are not reproduced.
- https://pigment.shapefactory.co/ — contrasting warm accents and soft neutral surfaces.
- https://fontpair.co/ — Space Grotesk headings with DM Sans interface text. Google Fonts loads these from index.html; system fonts remain available offline.
- https://httpster.net/ — spacious typography and a clear grid, adapted to an operational dashboard.
- https://gsap.com/ and https://reactbits.dev/ — restrained entrance motion, aurora background and pointer spotlight inspiration. These effects use original CSS and React event handlers; neither library is installed.

## Palette

| Role | Hex |
| --- | --- |
| Canvas | #100C12 |
| Panel | #19141D |
| Panel border | #392D3B |
| Primary text | #F8F1F5 |
| Secondary text | #B9ADBC |
| Brand accent | #FFA398 |
| Normal / healthy | #74D7BC |
| Warning | #F3C778 |
| Anomaly / error | #FF758D |
| Secondary accent | #C4ABFF |

## Implementation

- `frontend/src/theme.css` contains the shared visual theme and responsive styles.
- `frontend/src/styles.css` retains layout coverage for every page, with warm neutral colors and larger small text.
- `frontend/src/theme.ts` centralizes chart series and tooltip colors.
- `frontend/src/components/DesignEffects.tsx` contains the decorative background, pointer spotlight handler and linked pipeline banner.
- Shared cards, navigation, buttons, tables, filters and access settings use the new styling.
- The welcome screen opens access settings on request; it no longer obscures the initial view with an automatic modal.
- The footer connection dot is neutral until the database reports ready.

The design preserves API-backed values, model approval logic and alert actions. The pipeline banner contains navigation links, not simulated telemetry.

Reduced-motion preferences disable animation and pointer tracking. Keyboard focus, a skip link and forced-color fallback are included. The system-status navigation item is now a keyboard-accessible button.

## Run locally

From `frontend`, install the pinned packages with `npm ci`, then run `npm run dev`. The existing gateway configuration is still required for live data. `npm run build` and `npm test` are the existing validation commands.

During this edit, dependency installation was blocked by DNS resolution of registry.npmjs.org. The production build could not run because TypeScript was therefore unavailable. Browser rendering has not been verified in this environment.
