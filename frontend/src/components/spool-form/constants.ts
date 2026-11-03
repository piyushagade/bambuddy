import type { ColorPreset } from './types';

// Material options
export const MATERIALS = [
  'PLA', 'PETG', 'ABS', 'TPU', 'ASA', 'PC', 'PA', 'PVA', 'HIPS',
  'PA-CF', 'PETG-CF', 'PLA-CF',
];

// Common spool weights
export const WEIGHTS = [250, 500, 750, 1000, 2000, 3000];

// Default brand options (will be augmented with cloud presets)
export const DEFAULT_BRANDS = [
  'Bambu', 'PolyLite', 'PolyTerra', 'eSUN', 'Overture',
  'Fiberon', 'SUNLU', 'Inland', 'Hatchbox', 'Generic',
];

// Known filament variants/subtypes
export const KNOWN_VARIANTS = [
  'Basic', 'Matte', 'Silk', 'Silk+', 'Tough', 'Tough+', 'HF', 'High Flow', 'Engineering',
  // CF (Carbon Fiber) / GF (Glass Fiber) — match Bambu's PETG-CF / PA6-GF /
  // ABS-GF naming so users adding a third-party CF/GF spool can pick the
  // base material + subtype here instead of needing a -CF Material entry
  // (#1345).
  'CF', 'GF',
  'Galaxy', 'Glow', 'Marble', 'Metal', 'Rainbow', 'Sparkle', 'Wood',
  'Translucent', 'Transparent', 'Clear', 'Lite', 'Pro', 'Plus', 'Max',
  'Super', 'Ultra', 'Flex', 'Soft', 'Hard', 'Strong', 'Impact',
  'Heat Resistant', 'UV Resistant', 'ESD', 'Conductive', 'Magnetic',
  'Gradient', 'Dual Color', 'Tri Color', 'Multicolor',
];

// Quick color swatches - most common colors (shown by default).
// `Clear` is the only 8-char preset (alpha 00) — native `<input type="color">`
// cannot pick alpha, so a dedicated preset is the only way to set it (#1545).
export const QUICK_COLORS: ColorPreset[] = [
  // Neutrals & Clear
  { name: 'Black', hex: '000000' },
  { name: 'White', hex: 'FFFFFF' },
  { name: 'Clear', hex: '00000000' },
  { name: 'Silver', hex: 'C0C0C0' },
  { name: 'Gray', hex: '808080' },
  // Chromatic (rainbow order) + Brown
  { name: 'Red', hex: 'FF0000' },
  { name: 'Orange', hex: 'FFA500' },
  { name: 'Yellow', hex: 'FFFF00' },
  { name: 'Green', hex: '00AE42' },
  { name: 'Blue', hex: '0066FF' },
  { name: 'Purple', hex: '8B00FF' },
  { name: 'Pink', hex: 'FF69B4' },
  { name: 'Brown', hex: '8B4513' },
];

// Full color palette (shown when expanded), sorted continuously by family:
// Black, White, Clear -> Neutrals (light to dark) -> Rainbow (Reds, Oranges,
// Yellows, Greens, Cyans, Blues, Purples, Pinks) -> Browns/Beiges.
export const ALL_COLORS: ColorPreset[] = [
  // Black, White, Clear & Neutrals (light -> dark)
  { name: 'Black', hex: '000000' },
  { name: 'White', hex: 'FFFFFF' },
  { name: 'Clear', hex: '00000000' },
  { name: 'Ivory', hex: 'FFFFF0' },
  { name: 'Jade White', hex: 'E8E8E8' },
  { name: 'Light Gray', hex: 'D3D3D3' },
  { name: 'Silver', hex: 'C0C0C0' },
  { name: 'Gray', hex: '808080' },
  { name: 'Titan Gray', hex: '5A5A5A' },
  { name: 'Dark Gray', hex: '404040' },
  { name: 'Charcoal', hex: '36454F' },
  // Reds
  { name: 'Maroon', hex: '800000' },
  { name: 'Dark Red', hex: '8B0000' },
  { name: 'Crimson', hex: 'DC143C' },
  { name: 'Red', hex: 'FF0000' },
  { name: 'Salmon', hex: 'FA8072' },
  { name: 'Coral', hex: 'FF7F50' },
  // Oranges
  { name: 'Dark Orange', hex: 'FF8C00' },
  { name: 'Orange', hex: 'FFA500' },
  { name: 'Peach', hex: 'FFDAB9' },
  // Yellows
  { name: 'Gold', hex: 'FFD700' },
  { name: 'Yellow', hex: 'FFFF00' },
  { name: 'Lemon', hex: 'FFF44F' },
  { name: 'Khaki', hex: 'F0E68C' },
  // Greens
  { name: 'Olive', hex: '808000' },
  { name: 'Forest Green', hex: '228B22' },
  { name: 'Green', hex: '00AE42' },
  { name: 'Lime', hex: '32CD32' },
  { name: 'Mint', hex: '98FF98' },
  // Teals & Cyans
  { name: 'Teal', hex: '008080' },
  { name: 'Turquoise', hex: '40E0D0' },
  { name: 'Cyan', hex: '00FFFF' },
  // Blues
  { name: 'Navy', hex: '000080' },
  { name: 'Blue', hex: '0066FF' },
  { name: 'Royal Blue', hex: '4169E1' },
  { name: 'Sky Blue', hex: '87CEEB' },
  // Purples
  { name: 'Indigo', hex: '4B0082' },
  { name: 'Purple', hex: '8B00FF' },
  { name: 'Magenta', hex: 'FF00FF' },
  { name: 'Violet', hex: 'EE82EE' },
  { name: 'Plum', hex: 'DDA0DD' },
  { name: 'Lavender', hex: 'E6E6FA' },
  // Pinks
  { name: 'Rose', hex: 'FF007F' },
  { name: 'Hot Pink', hex: 'FF1493' },
  { name: 'Pink', hex: 'FF69B4' },
  { name: 'Blush', hex: 'FFB6C1' },
  // Browns & Beiges
  { name: 'Brown', hex: '8B4513' },
  { name: 'Chocolate', hex: 'D2691E' },
  { name: 'Tan', hex: 'D2B48C' },
  { name: 'Beige', hex: 'F5F5DC' },
];

// Extended color palette (kept exported for compatibility)
export const EXTENDED_COLORS: ColorPreset[] = ALL_COLORS.filter(
  (c) => !QUICK_COLORS.some((q) => q.hex === c.hex),
);

// Local storage keys
export const RECENT_COLORS_KEY = 'bambuddy-recent-colors';
export const MAX_RECENT_COLORS = 8;

/**
 * The nozzle sizes Bambu sells, smallest first.
 *
 * The spool form offers a filament preset and a K profile for every one of
 * them, not only the size currently screwed into the machine: a spool is
 * configured once and nozzles get swapped, and the calibration table on the
 * printer keeps entries per diameter regardless of what is fitted right now.
 */
export const STANDARD_NOZZLE_DIAMETERS = ['0.2', '0.4', '0.6', '0.8'];
