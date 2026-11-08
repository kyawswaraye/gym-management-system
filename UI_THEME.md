# GymPilot light theme

Design system for the staff web app. Premium fitness SaaS: white and light gray, one emerald accent, quiet surfaces, exact values below.

This is the target. Do not restyle application pages until it is approved.

Stack the values land in later: Tailwind CSS 4 and semantic classes in `web/src/app/globals.css`. Type is already Noto Sans Latin (`--font-latin`) and Noto Sans Myanmar (`--font-myanmar`). The mobile theme is separate.

## Principles

- Canvas is light gray. Surfaces are white. Structure comes from a 1px border.
- Emerald is the brand color, the primary action, the active nav item, and the positive status.
- Amber is warning. Red is danger. Every other hue is excluded.
- One primary button per toolbar or dialog. The label carries the verb.
- Type stays at weight 400, 500, or 600.
- Motion changes color and opacity only.

Leave out: gradients, tinted or rainbow cards, colored icon tiles, glow shadows, blue links, and a unique color for create, edit, archive, or save.

## 1. Color palette

Components use the semantic tokens. The ramps exist so those tokens have a defined source. Do not pick a ramp step that is not listed as a semantic token.

### Neutral

| Token | Hex | Role |
| --- | --- | --- |
| `white` | `#FFFFFF` | Surface, inputs, sidebar, dialogs |
| `canvas` | `#F7F8F9` | Page background |
| `inset` | `#F3F5F6` | Table header, secondary button hover, read-only fill |
| `hover` | `#EEF0F2` | Row hover, ghost hover |
| `border` | `#E5E7EB` | Cards, dividers, sidebar, default input stroke |
| `border-strong` | `#D1D5DB` | Input hover, stronger separators |
| `faint` | `#8B939A` | Placeholders, disabled labels, idle chevrons |
| `muted` | `#5F676E` | Secondary text, help text, icons at rest |
| `text-secondary` | `#3A4148` | Emphasis inside secondary text, avatar initials |
| `text` | `#1A1D1F` | Headings and body |

### Emerald

| Token | Hex | Role |
| --- | --- | --- |
| `emerald-50` | `#F1F8F4` | Active nav, positive badge, selected row |
| `emerald-100` | `#DCEFE4` | Positive badge border, success alert border |
| `emerald-600` | `#0F7A56` | Primary button, focus ring, brand accent |
| `emerald-700` | `#0C6547` | Primary hover, links, text on emerald tints |
| `emerald-800` | `#094E37` | Primary pressed |

White on `emerald-600` is `#FFFFFF`. Contrast is WCAG AA.

### Amber

| Token | Hex | Role |
| --- | --- | --- |
| `amber-50` | `#FBF7EF` | Warning badge and alert fill |
| `amber-200` | `#E7D3A4` | Warning border |
| `amber-700` | `#8A5A10` | Warning text |
| `amber-800` | `#6E470C` | Warning text hover, if a warning control is interactive |

### Red

| Token | Hex | Role |
| --- | --- | --- |
| `red-50` | `#FBF3F3` | Danger badge, error alert, danger hover fill |
| `red-200` | `#E7C8CB` | Danger border, input error stroke |
| `red-600` | `#B4232C` | Solid danger button |
| `red-700` | `#9B2330` | Danger text, error message, required mark |
| `red-800` | `#7F1D28` | Solid danger hover |

White on `red-600` is `#FFFFFF`. Contrast is WCAG AA.

### Semantic aliases

| Alias | Value | Use |
| --- | --- | --- |
| `--canvas` | `#F7F8F9` | `body` background |
| `--surface` | `#FFFFFF` | Cards, sidebar, dialogs, inputs |
| `--inset` | `#F3F5F6` | Quiet fills |
| `--hover` | `#EEF0F2` | Hover fills |
| `--border` | `#E5E7EB` | 1px strokes |
| `--border-strong` | `#D1D5DB` | Hover strokes |
| `--text` | `#1A1D1F` | Primary copy |
| `--muted` | `#5F676E` | Secondary copy |
| `--faint` | `#8B939A` | Tertiary copy |
| `--primary` | `#0F7A56` | Fill and focus |
| `--primary-hover` | `#0C6547` | Primary hover and link |
| `--primary-active` | `#094E37` | Primary pressed |
| `--primary-soft` | `#F1F8F4` | Soft emerald fill |
| `--primary-line` | `#DCEFE4` | Soft emerald border |
| `--warning-soft` | `#FBF7EF` | Soft amber fill |
| `--warning-text` | `#8A5A10` | Amber copy |
| `--warning-line` | `#E7D3A4` | Amber border |
| `--danger` | `#B4232C` | Solid danger fill |
| `--danger-hover` | `#7F1D28` | Solid danger hover |
| `--danger-soft` | `#FBF3F3` | Soft red fill |
| `--danger-text` | `#9B2330` | Red copy |
| `--danger-line` | `#E7C8CB` | Red border |
| `--scrim` | `rgb(26 29 31 / 40%)` | Dialog backdrop |
| `--shadow-overlay` | `0 8px 24px rgb(26 29 31 / 8%)` | Menus and dialogs |

Usage:

- Links are `--primary-hover`. Underline appears on hover.
- A page may use emerald on the primary button, the active nav item, or one key figure. A card background stays white.
- Status, alerts, and buttons draw from the four roles only: neutral, emerald, amber, red.

## 2. Typography

Family: `var(--font-latin), var(--font-myanmar), ui-sans-serif, sans-serif`.

Smoothing: antialiased. `font-synthesis: none`.

| Style | Size | Line height | Weight | Tracking | Use |
| --- | --- | --- | --- | --- | --- |
| Display | 28px | 34px | 600 | `-0.02em` | Page title, English only |
| Title | 18px | 24px | 600 | `-0.01em` | Card title, dialog title |
| Body | 14px | 21px | 400 | `0` | Default copy, table cells, inputs |
| Body strong | 14px | 21px | 600 | `0` | Names, selected values |
| Label | 13px | 18px | 600 | `0` | Field labels, table headers |
| Caption | 12px | 16px | 500 | `0` | Help text, email, meta |
| Badge | 12px | 16px | 600 | `0` | Status pills |

English page titles use display tracking. Myanmar sets tracking to `0` everywhere.

Myanmar (`html[lang="my"]`):

| Element | Line height |
| --- | --- |
| Body | 1.75 |
| Inputs, selects, textareas | 1.65 |
| Buttons | 1.6 |

Numbers, money, quantities, dates, and IDs use `font-variant-numeric: tabular-nums`. Weights 700, 800, and 900 are excluded.

## 3. Spacing

Base unit is 4px. These are the only steps.

| Token | Value |
| --- | --- |
| `space-1` | 4px |
| `space-2` | 8px |
| `space-3` | 12px |
| `space-4` | 16px |
| `space-5` | 20px |
| `space-6` | 24px |
| `space-8` | 32px |
| `space-10` | 40px |
| `space-12` | 48px |

| Context | Value |
| --- | --- |
| Icon to label | 8px |
| Label to control | 4px |
| Help text under a control | 4px |
| Stacked fields | 16px |
| Inline controls | 8px |
| Toolbar gap | 12px |
| Card padding | 20px |
| Gap between cards | 16px |
| Section gap inside a card | 16px |
| Space under the page header | 24px |
| Dialog padding | 24px |
| Dialog footer gap | 12px |
| Page padding, below `md` | 20px |
| Page padding, `md` and up | 32px |
| Sidebar padding | 24px |
| Table cell padding | 12px 16px |
| Nav item padding | 10px 12px |

## 4. Layout

### App shell

| Property | Value |
| --- | --- |
| Background | `--canvas` |
| Minimum height | `100vh` |
| Sidebar width | 256px |
| Sidebar placement, `md` and up | Fixed, full height, left |
| Main offset when sidebar is open | `margin-left: 256px` from `md` |
| Main max width | 1600px |
| Main alignment | Centered |
| Main padding | 20px below `md`, 32px from `md` |
| Main top padding when sidebar is closed | 64px, so content clears the open button |

### Breakpoints

| Name | Width | Behavior |
| --- | --- | --- |
| default | under 640px | Single column, 20px page padding |
| `sm` | 640px | Header actions may sit on one row |
| `md` | 768px | Sidebar is fixed. Page padding becomes 32px |
| `lg` | 1024px | Detail pages may use a content column plus a 320px side column, gap 16px |
| `xl` | 1280px | Same grid, wider content |

### Page stack

1. Page header
2. Optional toolbar (search, filters, secondary actions), gap 12px, margin-bottom 16px
3. One or more cards, or one table card

### Z-index

| Layer | Value |
| --- | --- |
| Sidebar | 30 |
| Sidebar toggle | 40 |
| Dropdown and account menu | 50 |
| Dialog scrim and dialog | 100 |

## 5. Radius

| Token | Value | Use |
| --- | --- | --- |
| `xs` | 4px | Checkbox, radio mark |
| `sm` | 6px | Inputs, compact buttons, menu items |
| `md` | 8px | Buttons, nav items, alerts |
| `lg` | 12px | Cards, dialogs, menus |
| `full` | 999px | Badges, avatars, switches |

Cards, buttons, and inputs stay on this scale. 16px and 24px radii are excluded.

## 6. Elevation

| Token | Value | Use |
| --- | --- | --- |
| Flat | `none` | Cards, sidebar, inputs, buttons, tables |
| Overlay | `0 8px 24px rgb(26 29 31 / 8%)` | Dialogs and menus |

Surfaces separate with `--border`. Colored shadows, glows, and hover lift are excluded.

## 7. Buttons

### Shared

| Property | Default | Compact |
| --- | --- | --- |
| Height | 44px | 32px |
| Padding | 0 16px | 0 12px |
| Radius | 8px | 6px |
| Font | 14px / 600 | 13px / 600 |
| Icon | 16px, 8px before the label | 16px |
| Border | 1px solid | 1px solid |
| Cursor | pointer | pointer |
| Transition | background-color, border-color, color, 150ms ease | same |
| Disabled | opacity 0.55, cursor not-allowed | same |
| Focus | 2px solid `--primary`, offset 2px | same |

Myanmar buttons use line-height 1.6. Compact buttons set `white-space: nowrap`.

### Primary

| State | Background | Text | Border |
| --- | --- | --- | --- |
| Rest | `#0F7A56` | `#FFFFFF` | transparent |
| Hover | `#0C6547` | `#FFFFFF` | transparent |
| Pressed | `#094E37` | `#FFFFFF` | transparent |

Use for the single main action: Save, Create, Confirm. One primary button per header or dialog footer.

### Secondary

| State | Background | Text | Border |
| --- | --- | --- | --- |
| Rest | `#FFFFFF` | `#1A1D1F` | `#E5E7EB` |
| Hover | `#F3F5F6` | `#1A1D1F` | `#D1D5DB` |
| Pressed | `#EEF0F2` | `#1A1D1F` | `#D1D5DB` |

Use for Cancel and alternate actions.

### Ghost

| State | Background | Text | Border |
| --- | --- | --- | --- |
| Rest | transparent | `#1A1D1F` | transparent |
| Hover | `#F3F5F6` | `#1A1D1F` | transparent |
| Pressed | `#EEF0F2` | `#1A1D1F` | transparent |

Use for row actions and low-emphasis controls. Edit, view, and archive use ghost. The label names the action.

### Danger

| State | Background | Text | Border |
| --- | --- | --- | --- |
| Rest | `#FFFFFF` | `#9B2330` | `#E7C8CB` |
| Hover | `#FBF3F3` | `#9B2330` | `#E7C8CB` |
| Pressed | `#F6E0E1` | `#7F1D28` | `#E7C8CB` |

Use for Delete, Void, and Revoke while the user is still on the page.

### Danger solid

| State | Background | Text | Border |
| --- | --- | --- | --- |
| Rest | `#B4232C` | `#FFFFFF` | transparent |
| Hover | `#7F1D28` | `#FFFFFF` | transparent |

Use only for the confirm button inside a destructive dialog.

### Icon button

36px square, radius 8px, ghost colors, 20px icon. The sidebar toggle uses this size with a secondary border: 1px `#E5E7EB`, white fill.

## 8. Forms

### Field

| Property | Value |
| --- | --- |
| Layout | Column |
| Label gap | 4px |
| Field gap | 16px |
| Help gap | 4px |

Two related fields may sit side by side from `sm`, with a 16px gap. A form section inside a card stacks at 16px.

### Label

13px, weight 600, color `--text`. Optional hint after the label is 12px `--muted`, weight 500. Required mark is `*` in `--danger-text`, 2px to the right of the label.

### Text input

| Property | Value |
| --- | --- |
| Height | 44px |
| Width | 100% |
| Padding | 0 12px |
| Radius | 6px |
| Background | `#FFFFFF` |
| Border | 1px solid `#E5E7EB` |
| Text | 14px / 400 / `#1A1D1F` |
| Placeholder | `#8B939A` |

| State | Treatment |
| --- | --- |
| Hover | Border `#D1D5DB` |
| Focus | Border `#0F7A56`, outline 2px solid `#0F7A56`, offset 1px. Box-shadow none |
| Error | Border `#E7C8CB`. Message 12px `#9B2330`, 4px below |
| Disabled | Background `#F3F5F6`, text `#8B939A`, cursor not-allowed |
| Read-only | Background `#F3F5F6`, text `#1A1D1F` |

Prefix or suffix text sits inside the field, 12px from the edge, 13px `--muted`. The input padding grows by 28px on that side.

### Textarea

Same stroke, type, radius, and states as the text input. Min-height 96px. Padding 12px. Resize vertical.

### Select

Same box as the text input. Native appearance removed. Padding-right 40px. Chevron is a 16px stroke icon, color `#5F676E`, placed 12px from the right. Disabled select uses the disabled input fill.

### Checkbox and radio

| Property | Value |
| --- | --- |
| Size | 16px |
| Radius | 4px checkbox, 999px radio |
| Border | 1px solid `#D1D5DB` |
| Background | `#FFFFFF` |
| Checked | Background `#0F7A56`, border `#0F7A56`, white mark |
| Label | 14px `#1A1D1F`, 8px to the right of the control |
| Hit area | Min-height 44px for a single choice on its own row |

### Search

A search field is a text input with a 16px search icon in `--muted`, 12px from the left, and 36px left padding. In a toolbar its max-width is 320px and it may shrink. The results menu uses the menu style below, anchored 4px under the field.

### Field group

A group label is a label style. The group is a white card section with 16px between controls. Description under the group label is caption, `--muted`.

## 9. Cards

| Property | Value |
| --- | --- |
| Background | `#FFFFFF` |
| Border | 1px solid `#E5E7EB` |
| Radius | 12px |
| Padding | 20px |
| Shadow | none |
| Width | 100% of the content column |

### Header

Title is the title style. Description is 14px `--muted`, 4px under the title, max-width 640px. Actions align to the end of the header row. Gap between title block and actions is 16px. Header margin-bottom is 16px when a body follows.

### Stat

Value is 24px, line-height 32px, weight 600, `--text`, `tabular-nums`. Label is 13px `--muted`, 4px above the value. Stats in a row use a 4-column grid from `lg`, 2 columns from `sm`, gap 16px. Each stat is its own card. At most one value on the page uses `#0C6547`.

### Dividers

1px solid `#E5E7EB`. A divider inside a card spans the padding edge to edge by using negative horizontal margin equal to the card padding, or by sitting outside the padded body. Space around a divider is 16px.

### Empty card

One sentence, 14px `--muted`. Optional action is a primary or secondary button, 16px below the sentence.

## 10. Tables

A table sits inside a card with padding 0. The card radius clips the header corners.

| Property | Value |
| --- | --- |
| Width | 100% |
| Border collapse | collapse |
| Outer border | none, the card supplies it |
| Vertical rules | none |
| Row divider | 1px solid `#E5E7EB` on each row |
| Header background | `#F3F5F6` |
| Header text | 12px, weight 600, `#5F676E` |
| Header padding | 12px 16px |
| Header transform | none |
| Cell text | 14px, weight 400, `#1A1D1F` |
| Cell padding | 12px 16px |
| Vertical align | middle |
| Row hover | `#EEF0F2` |
| Selected row | `#F1F8F4` |
| Numeric column | right aligned, `tabular-nums` |
| Action column | right aligned, gap 8px |

Header labels stay in sentence case. A sortable header uses a 16px chevron in `#8B939A`. The active sort chevron is `#5F676E`.

Links in cells are `#0C6547`, weight 600, no underline until hover.

Row actions are compact ghost buttons. A destructive row action is a compact danger button. The page-level primary action stays in the page header.

Empty table: one cell spanning all columns, padding 24px 16px, 14px `--muted`.

Wide tables scroll inside the card with `overflow-x: auto`. The header stays with the table. Sticky columns are excluded until a page truly needs one.

### Pagination

Sits under the table, inside the card, with 16px padding and a top border `#E5E7EB`. Layout is space-between, align center. The position label is 13px `--muted`. Previous and Next are compact secondary buttons, gap 8px. Hide a direction that has nowhere to go.

## 11. Navigation

### Sidebar

| Property | Value |
| --- | --- |
| Width | 256px |
| Background | `#FFFFFF` |
| Text | `#1A1D1F` |
| Right border | 1px solid `#E5E7EB` |
| Shadow | none |
| Padding | 24px |
| Below `md` | In flow, border-bottom instead of border-right, collapses by grid row to 0 |
| `md` and up | Fixed, full height, flex column |

### Brand row

Height of the row is content. Bottom border 1px `#E5E7EB`. Padding-bottom 16px. Margin-bottom 16px. Mark is 36px. Name is 14px, weight 600, `#1A1D1F`, truncated. Gap between mark and name is 10px.

### Section label

12px, weight 600, `#5F676E`. Margin-top 16px. Margin-bottom 8px. First section has no top margin. English may use uppercase with tracking `0.04em`. Myanmar stays as written, tracking 0.

### Nav item

| State | Background | Text | Weight | Radius |
| --- | --- | --- | --- | --- |
| Rest | transparent | `#1A1D1F` | 500 | 8px |
| Hover | `#F3F5F6` | `#1A1D1F` | 500 | 8px |
| Active | `#F1F8F4` | `#0C6547` | 600 | 8px |

Padding 10px 12px. Font 14px. Gap between items 4px. Icon 20px, color matches the text, 8px before the label. Active is `aria-current="page"`. Focus is the standard 2px `#0F7A56` outline, offset 2px.

### Footer

Margin-top auto. Border-top 1px `#E5E7EB`. Padding-top 12px. Account control is full width, radius 8px, padding 8px, ghost hover. Avatar is 32px circle, fill `#F3F5F6`, initials 13px weight 600 `#3A4148`. Name is 14px weight 600. Email is 12px `#5F676E`, truncated.

### Closed sidebar

The open button is fixed at top 12px, left 12px: 36px icon button, white, 1px `#E5E7EB`, overlay shadow so it stays visible on the canvas.

## 12. Status badges

| Property | Value |
| --- | --- |
| Display | inline-flex, center |
| Height | 22px |
| Padding | 0 8px |
| Radius | 999px |
| Font | 12px / 600 |
| Border | 1px solid |

| Tone | Fill | Text | Border | Statuses |
| --- | --- | --- | --- | --- |
| Positive | `#F1F8F4` | `#0C6547` | `#DCEFE4` | Active, posted, issued, paid, open, in stock |
| Warning | `#FBF7EF` | `#8A5A10` | `#E7D3A4` | Pending approval, on leave, suspended, low stock, expiring |
| Danger | `#FBF3F3` | `#9B2330` | `#E7C8CB` | Rejected, void, voided, revoked, terminated, overdue |
| Neutral | `#F3F5F6` | `#5F676E` | `#E5E7EB` | Draft, invited, ended, inactive, cancelled, closed |

A badge is text on a tint. Dots and icons are left off unless the badge is the only content in a narrow cell. Invited and cancelled use neutral.

## 13. Feedback

### Alert

| Property | Value |
| --- | --- |
| Padding | 12px 16px |
| Radius | 8px |
| Font | 14px / 400 |
| Border | 1px solid |
| Role | `alert` for errors, `status` for success |

| Tone | Fill | Text | Border |
| --- | --- | --- | --- |
| Error | `#FBF3F3` | `#9B2330` | `#E7C8CB` |
| Success | `#F1F8F4` | `#0C6547` | `#DCEFE4` |
| Warning | `#FBF7EF` | `#8A5A10` | `#E7D3A4` |
| Neutral | `#F3F5F6` | `#3A4148` | `#E5E7EB` |

Margin below an alert that sits over a form is 16px.

### Loading

14px `--muted`, role `status`. A block placeholder uses `#F3F5F6`, radius 8px, and a quiet opacity pulse. Skeleton blocks match the height of the content they replace: 44px for a control, 22px for a text line.

### Empty

14px `--muted`. One sentence. One optional button.

## 14. Overlays

### Dialog

| Property | Value |
| --- | --- |
| Width | `min(100% - 32px, 480px)` |
| Wide width | `min(100% - 32px, 640px)` when the form has two columns |
| Background | `#FFFFFF` |
| Border | 1px solid `#E5E7EB` |
| Radius | 12px |
| Padding | 24px |
| Shadow | overlay |
| Scrim | `rgb(26 29 31 / 40%)` |
| Title | 18px / 600 / `#1A1D1F` |
| Body | 14px, margin-top 8px |
| Footer | margin-top 24px, flex, end, gap 12px, wrap |

Order in the footer: secondary or ghost first, primary or danger-solid last. Body scroll locks while a modal dialog is open. Focus moves to the dialog and returns to the trigger on close.

### Menu

| Property | Value |
| --- | --- |
| Background | `#FFFFFF` |
| Border | 1px solid `#E5E7EB` |
| Radius | 12px |
| Padding | 6px |
| Shadow | overlay |
| Offset from trigger | 8px |
| Item padding | 8px 10px |
| Item radius | 6px |
| Item text | 14px / 500 / `#1A1D1F` |
| Item hover | `#F3F5F6` |
| Divider | 1px `#E5E7EB`, margin 4px 8px |
| Destructive item | text `#9B2330`, hover fill `#FBF3F3` |

## 15. Page header

| Property | Value |
| --- | --- |
| Margin-bottom | 24px |
| Padding-bottom | 24px |
| Border-bottom | 1px solid `#E5E7EB` |
| Eyebrow | 12px / 600 / `#0C6547` / tracking `0.06em` / uppercase in English |
| Title | Display style, 4px under the eyebrow when an eyebrow exists |
| Description | 14px / `#5F676E` / line-height 21px / max-width 640px / 8px under the title |
| Actions | Align end, gap 8px, wrap |

Compact header, for nested tools: no border, margin-bottom 12px, title 18px / 600 / `#1A1D1F`.

From `sm`, title and actions share one row, aligned to the end. Below `sm` they stack with a 12px gap.

## 16. Icons

One stroke set. Stroke 1.75px. Caps and joins round. Color `currentColor`.

| Size | Use |
| --- | --- |
| 20px | Sidebar, page-level icon buttons |
| 16px | Buttons, fields, chevrons, table actions |

The icon sits before the label with an 8px gap. Decorative icons are `aria-hidden`. A control that is only an icon has an accessible name. The gym or product mark is the only filled graphic. Emoji and icons inside colored tiles are excluded.

## 17. Focus and motion

| Property | Value |
| --- | --- |
| Focus ring | 2px solid `#0F7A56` |
| Focus offset | 2px |
| Input focus | See the input rule. The global ring does not double up |
| Duration | 150ms ease |
| Properties | background-color, border-color, color, opacity |
| Reduced motion | Transitions set to none under `prefers-reduced-motion: reduce` |

Hover does not move, scale, or shadow an element.

## 18. Component map

Use these recipes when building staff screens. Each recipe is only the tokens above.

| UI | Recipe |
| --- | --- |
| List page | Page header, toolbar, table card, pagination |
| Detail page | Page header, then cards in one column or a `lg` grid with a 320px side card |
| Create and edit | Dialog at 480px, or a card form if the page is the form |
| Destructive confirm | Dialog, copy in body, secondary Cancel, danger-solid confirm |
| Filters | Toolbar of selects and one search field. Active filter text is body strong. Clear is ghost |
| Account menu | Menu anchored to the sidebar footer |
| Language switch | Two compact buttons. The selected one is secondary with border `#0F7A56` and text `#0C6547`. The other is ghost |
| Money and IDs | Body or body strong, `tabular-nums` |
| Member or staff identity | 32px avatar, name in body strong, meta in caption |

New UI uses an existing recipe. A new color, radius, shadow, or type step needs an update to this file first.
