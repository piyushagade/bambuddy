import { useState, useRef, useEffect, useMemo } from 'react';
import { Search, Loader2, ChevronDown, Cloud, CloudOff, Shield, X, Users, UserPlus, Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { FilamentSectionProps, FilamentOption } from './types';
import { KNOWN_VARIANTS } from './constants';
import { parsePresetName } from './utils';
import { PresetSourceBadge } from './PresetPicker';
import { NumberInput } from '../NumberInput';

// The identity fields a slicer preset can auto-fill.
type PresetFilledField = 'material' | 'brand' | 'subtype';

// Split a dropdown's options into the ones the catalog/presets pair with the
// other field's current value and everything else (#1905). Suggestions are only
// a sort order — nothing is ever hidden, so an unusual-but-real combination
// (Elegoo ASA) stays one click away.
function splitSuggested(options: string[], suggested: string[]): { top: string[]; rest: string[] } {
  if (suggested.length === 0) return { top: [], rest: options };
  const keys = new Set(suggested.map(s => s.toLowerCase()));
  return {
    top: options.filter(o => keys.has(o.toLowerCase())),
    rest: options.filter(o => !keys.has(o.toLowerCase())),
  };
}

function GroupHeading({ label }: { label: string }) {
  return (
    <div className="px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-bambu-gray/70 bg-bambu-dark-tertiary/40">
      {label}
    </div>
  );
}

export function FilamentSection({
  formData,
  updateField,
  cloudAuthenticated,
  loadingCloudPresets,
  presetInputValue,
  setPresetInputValue,
  selectedPresetOption,
  filamentOptions,
  availableBrands,
  availableMaterials,
  suggestedBrands,
  suggestedMaterials,
  quickAdd,
  detailsRequired,
  quantity,
  onQuantityChange,
  errors,
  accessGroups = [],
  accessUsers = [],
  spoolmanMode = false,
}: FilamentSectionProps) {
  const { t } = useTranslation();
  const [presetDropdownOpen, setPresetDropdownOpen] = useState(false);
  const [brandDropdownOpen, setBrandDropdownOpen] = useState(false);
  const [subtypeDropdownOpen, setSubtypeDropdownOpen] = useState(false);
  const [materialDropdownOpen, setMaterialDropdownOpen] = useState(false);
  const [accessModalTab, setAccessModalTab] = useState<'groups' | 'users' | null>(null);
  const [brandSearch, setBrandSearch] = useState('');
  const [subtypeSearch, setSubtypeSearch] = useState('');
  const [materialSearch, setMaterialSearch] = useState('');
  const [groupSearch, setGroupSearch] = useState('');
  const [userSearch, setUserSearch] = useState('');
  const [labelInput, setLabelInput] = useState(String(formData.label_weight));
  const [isLabelFocused, setIsLabelFocused] = useState(false);
  // Which identity fields the currently selected preset filled in (#1905).
  // Anything outside this set was set by the user (or loaded from the spool
  // being edited) and must survive picking a preset.
  const [presetFilled, setPresetFilled] = useState<Set<PresetFilledField>>(new Set());
  const presetRef = useRef<HTMLDivElement>(null);
  const brandRef = useRef<HTMLDivElement>(null);
  const subtypeRef = useRef<HTMLDivElement>(null);
  const materialRef = useRef<HTMLDivElement>(null);

  // Close dropdowns on outside click
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (presetRef.current && !presetRef.current.contains(e.target as Node)) {
        setPresetDropdownOpen(false);
      }
      if (materialRef.current && !materialRef.current.contains(e.target as Node)) {
        setMaterialDropdownOpen(false);
      }
      if (brandRef.current && !brandRef.current.contains(e.target as Node)) {
        setBrandDropdownOpen(false);
      }
      if (subtypeRef.current && !subtypeRef.current.contains(e.target as Node)) {
        setSubtypeDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  // Filtered presets based on search
  const filteredPresets = useMemo(() => {
    if (!presetInputValue) return filamentOptions;
    const search = presetInputValue.toLowerCase();
    return filamentOptions.filter(o =>
      o.displayName.toLowerCase().includes(search) ||
      o.code.toLowerCase().includes(search),
    );
  }, [filamentOptions, presetInputValue]);

  // Filtered brands
  const filteredBrands = useMemo(() => {
    if (!brandSearch) return availableBrands;
    const search = brandSearch.toLowerCase();
    const filtered = availableBrands.filter(b => b.toLowerCase().includes(search));
    // Sort: exact match first, then others
    return filtered.sort((a, b) => {
      const aExact = a.toLowerCase() === search;
      const bExact = b.toLowerCase() === search;
      if (aExact && !bExact) return -1;
      if (!aExact && bExact) return 1;
      return a.localeCompare(b);
    });
  }, [availableBrands, brandSearch]);

  const filteredVariants = useMemo(() => {
    if (!subtypeSearch) return KNOWN_VARIANTS;
    const search = subtypeSearch.toLowerCase();
    return KNOWN_VARIANTS.filter(v => v.toLowerCase().includes(search));
  }, [subtypeSearch]);

  const filteredMaterials = useMemo(() => {
    if (!materialSearch) return availableMaterials;
    const search = materialSearch.toLowerCase();
    const filtered = availableMaterials.filter(m => m.toLowerCase().includes(search));
    // Sort: exact match first, then others
    return filtered.sort((a, b) => {
      const aExact = a.toLowerCase() === search;
      const bExact = b.toLowerCase() === search;
      if (aExact && !bExact) return -1;
      if (!aExact && bExact) return 1;
      return a.localeCompare(b);
    });
  }, [materialSearch, availableMaterials]);

  const brandGroups = useMemo(
    () => splitSuggested(filteredBrands, suggestedBrands),
    [filteredBrands, suggestedBrands],
  );

  const materialGroups = useMemo(
    () => splitSuggested(filteredMaterials, suggestedMaterials),
    [filteredMaterials, suggestedMaterials],
  );

  useEffect(() => {
    if (!isLabelFocused) {
      setLabelInput(String(formData.label_weight));
    }
  }, [formData.label_weight, isLabelFocused]);

  // Handle preset selection
  const handlePresetSelect = (option: FilamentOption) => {
    updateField('slicer_filament', option.code);
    setPresetInputValue(option.displayName);
    setPresetDropdownOpen(false);

    // Auto-fill material, brand and subtype from the preset name — but only
    // where the value is still empty or was itself put there by the previously
    // selected preset (#1905). Overwriting unconditionally silently rewrote the
    // spool's own manufacturer ("Elegoo" → "Generic") whenever the user was
    // forced to assign a preset, so the spool vanished from where they filed it.
    // Switching between presets still replaces what the earlier one filled in.
    const parsed = parsePresetName(option.name);
    const filled = new Set<PresetFilledField>();
    const entries: [PresetFilledField, string][] = [
      ['material', parsed.material],
      ['brand', parsed.brand],
      ['subtype', parsed.variant],
    ];
    for (const [field, value] of entries) {
      if (!value) continue;
      if (formData[field] && !presetFilled.has(field)) continue;
      updateField(field, value);
      filled.add(field);
    }
    setPresetFilled(filled);
  };

  // A manual edit to an identity field takes it back out of the preset's hands.
  const updateIdentityField = (field: PresetFilledField, value: string) => {
    if (presetFilled.has(field)) {
      setPresetFilled(prev => {
        const next = new Set(prev);
        next.delete(field);
        return next;
      });
    }
    updateField(field, value);
  };

  const renderBrandOption = (brand: string) => (
    <button
      key={brand}
      type="button"
      className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary ${
        formData.brand === brand ? 'bg-bambu-green/10 text-bambu-green' : 'text-white'
      }`}
      onClick={() => {
        updateIdentityField('brand', brand);
        setBrandDropdownOpen(false);
        setBrandSearch('');
      }}
    >
      {brand}
    </button>
  );

  const renderMaterialOption = (material: string) => (
    <button
      key={material}
      type="button"
      className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary ${
        formData.material === material ? 'bg-bambu-green/10 text-bambu-green' : 'text-white'
      }`}
      onClick={() => {
        updateIdentityField('material', material);
        setMaterialDropdownOpen(false);
        setMaterialSearch('');
      }}
    >
      {material}
    </button>
  );

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-4">
      {/* Cloud status indicator */}
      {!quickAdd && (
        <div className="sm:col-span-2 flex items-center gap-2 text-xs text-bambu-gray -mb-2">
          {loadingCloudPresets ? (
            <><Loader2 className="w-3 h-3 animate-spin" /> {t('inventory.loadingPresets')}</>
          ) : cloudAuthenticated ? (
            <><Cloud className="w-3 h-3 text-bambu-green" /> {t('inventory.cloudConnected')}</>
          ) : (
            <><CloudOff className="w-3 h-3" /> {t('inventory.cloudNotConnected')}</>
          )}
        </div>
      )}

      {/* Slicer Preset (autocomplete) — hidden in quick-add mode */}
      {!quickAdd && (
        <div className="sm:col-span-2">
          <label className="block text-sm font-medium text-bambu-gray mb-1">
            {t('inventory.slicerPreset')}{detailsRequired && ' *'}
          </label>
          <div className="relative" ref={presetRef}>
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
            <input
              type="text"
              className="w-full pl-9 pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
              placeholder={t('inventory.searchPresets')}
              value={presetInputValue}
              onChange={(e) => {
                setPresetInputValue(e.target.value);
                setPresetDropdownOpen(true);
              }}
              onFocus={() => {
                setPresetDropdownOpen(true);
                setPresetInputValue('');
              }}
            />
            {presetDropdownOpen && (
              <div className="absolute z-50 w-full mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-64 overflow-y-auto">
                {filteredPresets.length === 0 ? (
                  <div className="px-3 py-2 text-sm text-bambu-gray">{t('inventory.noPresetsFound')}</div>
                ) : (
                  filteredPresets.map(option => (
                    <button
                      key={`${option.code}::${option.name}`}
                      type="button"
                      className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary flex items-center gap-2 ${
                        selectedPresetOption?.code === option.code
                          ? 'bg-bambu-green/10 text-bambu-green'
                          : 'text-white'
                      }`}
                      onClick={() => handlePresetSelect(option)}
                    >
                      {/* Same origin badge as the Printers tab and the
                          Configure AMS Slot modal: the same filament exists as
                          a cloud preset, an imported one and a built-in, and
                          which is picked decides what reaches the printer. */}
                      <span className="flex-1 min-w-0 truncate" title={option.displayName}>
                        {option.displayName}
                      </span>
                      <PresetSourceBadge source={option.source} />
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
          {selectedPresetOption && (
            <div className="mt-1 text-xs text-bambu-gray">
              {t('inventory.selectedPreset')}: <span className="font-mono text-bambu-green">{selectedPresetOption.code}</span>
            </div>
          )}
          {errors?.slicer_filament && (
            <p className="mt-1 text-xs text-red-700 dark:text-red-400">{errors.slicer_filament}</p>
          )}
        </div>
      )}

      {/* Material */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.material')} *</label>
        <div className="relative" ref={materialRef}>
          <input
            type="text"
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
            placeholder={t('inventory.selectMaterial')}
            value={materialDropdownOpen ? materialSearch : formData.material}
            onChange={(e) => {
              setMaterialSearch(e.target.value);
              setMaterialDropdownOpen(true);
            }}
            onFocus={() => {
              setMaterialDropdownOpen(true);
              setMaterialSearch('');
            }}
          />
          <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
          {materialDropdownOpen && (
            <div className="absolute z-50 w-full mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-48 overflow-y-auto">
              {filteredMaterials.length === 0 ? (
                <div className="px-3 py-2 text-sm text-bambu-gray">{t('inventory.noResults')}</div>
              ) : (
                <>
                  {materialGroups.top.length > 0 && (
                    <>
                      <GroupHeading label={t('inventory.suggestedOptions')} />
                      {materialGroups.top.map(renderMaterialOption)}
                    </>
                  )}
                  {materialGroups.rest.length > 0 && (
                    <>
                      {materialGroups.top.length > 0 && <GroupHeading label={t('inventory.allOptions')} />}
                      {materialGroups.rest.map(renderMaterialOption)}
                    </>
                  )}
                </>
              )}
              {/* Allow custom material */}
              {materialSearch && !filteredMaterials.includes(materialSearch) && (
                <button
                  type="button"
                  className="w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary text-bambu-green border-t border-bambu-dark-tertiary"
                  onClick={() => {
                    updateIdentityField('material', materialSearch);
                    setMaterialDropdownOpen(false);
                    setMaterialSearch('');
                  }}
                >
                  {t('inventory.useCustomMaterial', { material: materialSearch })}
                </button>
              )}
            </div>
          )}
        </div>
        {errors?.material && (
          <p className="mt-1 text-xs text-red-700 dark:text-red-400">{errors.material}</p>
        )}
      </div>

      {/* Brand (dropdown with search) */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">
          {t('inventory.brand')}{detailsRequired && ' *'}
        </label>
          <div className="relative" ref={brandRef}>
            <input
              type="text"
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
              placeholder={t('inventory.searchBrand')}
              value={brandDropdownOpen ? brandSearch : formData.brand}
              onChange={(e) => {
                setBrandSearch(e.target.value);
                setBrandDropdownOpen(true);
              }}
              onFocus={() => {
                setBrandDropdownOpen(true);
                setBrandSearch('');
              }}
            />
            <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
            {brandDropdownOpen && (
              <div className="absolute z-50 w-full mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-48 overflow-y-auto">
                {filteredBrands.length === 0 ? (
                  <div className="px-3 py-2 text-sm text-bambu-gray">{t('inventory.noResults')}</div>
                ) : (
                  <>
                    {brandGroups.top.length > 0 && (
                      <>
                        <GroupHeading label={t('inventory.suggestedOptions')} />
                        {brandGroups.top.map(renderBrandOption)}
                      </>
                    )}
                    {brandGroups.rest.length > 0 && (
                      <>
                        {brandGroups.top.length > 0 && <GroupHeading label={t('inventory.allOptions')} />}
                        {brandGroups.rest.map(renderBrandOption)}
                      </>
                    )}
                  </>
                )}
                {/* Allow custom brand */}
                {brandSearch && !filteredBrands.includes(brandSearch) && (
                  <button
                    type="button"
                    className="w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary text-bambu-green border-t border-bambu-dark-tertiary"
                    onClick={() => {
                      updateIdentityField('brand', brandSearch);
                      setBrandDropdownOpen(false);
                      setBrandSearch('');
                    }}
                  >
                    {t('inventory.useCustomBrand', { brand: brandSearch })}
                  </button>
                )}
              </div>
            )}
          </div>
          {errors?.brand && (
            <p className="mt-1 text-xs text-red-700 dark:text-red-400">{errors.brand}</p>
          )}
      </div>

      {/* Variant / Subtype */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">
          {t('inventory.subtype')}{detailsRequired && ' *'}
        </label>
          <div className="relative" ref={subtypeRef}>
            <input
              type="text"
              value={subtypeDropdownOpen ? subtypeSearch : formData.subtype}
              onChange={(e) => {
                setSubtypeSearch(e.target.value);
                setSubtypeDropdownOpen(true);
              }}
              onFocus={() => {
                setSubtypeDropdownOpen(true);
                setSubtypeSearch('');
              }}
              placeholder="Basic, Matte, Silk..."
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
            />
            <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
            {subtypeDropdownOpen && (
              <div className="absolute z-50 w-full mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-48 overflow-y-auto">
                {filteredVariants.length === 0 ? (
                  <div className="px-3 py-2 text-sm text-bambu-gray">{t('inventory.noResults')}</div>
                ) : (
                  filteredVariants.map(variant => (
                    <button
                      key={variant}
                      type="button"
                      className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary ${
                        formData.subtype === variant ? 'bg-bambu-green/10 text-bambu-green' : 'text-white'
                      }`}
                      onClick={() => {
                        updateIdentityField('subtype', variant);
                        setSubtypeDropdownOpen(false);
                        setSubtypeSearch('');
                      }}
                    >
                      {variant}
                    </button>
                  ))
                )}
                {subtypeSearch && !KNOWN_VARIANTS.some(v => v.toLowerCase() === subtypeSearch.toLowerCase().trim()) && (
                  <button
                    type="button"
                    className="w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary text-bambu-green border-t border-bambu-dark-tertiary"
                    onClick={() => {
                      updateIdentityField('subtype', subtypeSearch);
                      setSubtypeDropdownOpen(false);
                      setSubtypeSearch('');
                    }}
                  >
                    {t('inventory.useCustomBrand', { brand: subtypeSearch })}
                  </button>
                )}
              </div>
            )}
          </div>
          {errors?.subtype && (
            <p className="mt-1 text-xs text-red-700 dark:text-red-400">{errors.subtype}</p>
          )}
      </div>

      {/* Label Weight */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.labelWeight')}</label>
        <div className="relative">
          <input
            type="number"
            className="w-full px-3 py-2 pr-7 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
            value={labelInput}
            min={0}
            onFocus={() => setIsLabelFocused(true)}
            onChange={(e) => setLabelInput(e.target.value)}
            onBlur={() => {
              setIsLabelFocused(false);
              const raw = labelInput.trim();
              const next = Number(raw);
              if (!raw || !Number.isFinite(next) || next < 0) {
                setLabelInput(String(formData.label_weight));
                return;
              }
              const rounded = Math.round(next);
              updateField('label_weight', rounded);
              setLabelInput(String(rounded));
            }}
          />
          <span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-bambu-gray">g</span>
        </div>
      </div>

      {/* Quantity — only in quick-add mode */}
      {quickAdd && (
        <div className="sm:col-span-2">
          <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.quantity')}</label>
          <NumberInput
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
            value={quantity}
            min={1}
            max={100}
            onChange={onQuantityChange}
            fallback={1}
          />
        </div>
      )}

      {/* Access Control (Authorized Groups & Users) — internal inventory only */}
      {!spoolmanMode && (
        <div className="sm:col-span-2 pt-2 border-t border-bambu-dark-tertiary">
          <div className="flex items-center gap-1.5 mb-1">
            <Shield className="w-4 h-4 text-bambu-green" />
            <label className="text-sm font-medium text-white">
              {t('inventory.accessControl', 'Access Control (Authorized Groups & Users)')}
            </label>
          </div>
          <p className="text-xs text-bambu-gray mb-3">
            {t(
              'inventory.accessControlHint',
              'Restrict which groups (courses/cohorts) or users are allowed to print with this spool. Leave empty to allow everyone.',
            )}
          </p>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {/* Allowed Groups / Courses */}
            <div className="bg-bambu-dark border border-bambu-dark-tertiary rounded-lg p-2.5 flex flex-col justify-between gap-2">
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-xs font-medium text-bambu-gray flex items-center gap-1.5">
                    <Users className="w-3.5 h-3.5 text-bambu-green" />
                    {t('inventory.allowedGroups', 'Allowed Groups / Courses')}
                  </label>
                  {formData.allowed_group_ids.length > 0 && (
                    <span className="text-[11px] px-1.5 py-0.5 rounded bg-bambu-green/15 text-bambu-green font-medium">
                      {formData.allowed_group_ids.length}
                    </span>
                  )}
                </div>
                {formData.allowed_group_ids.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5 max-h-24 overflow-y-auto py-0.5">
                    {formData.allowed_group_ids.map((gid) => {
                      const group = accessGroups.find((g) => g.id === gid);
                      return (
                        <span
                          key={gid}
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-bambu-green/15 text-bambu-green border border-bambu-green/30"
                        >
                          {group ? group.name : `Group #${gid}`}
                          <button
                            type="button"
                            onClick={() =>
                              updateField(
                                'allowed_group_ids',
                                formData.allowed_group_ids.filter((id) => id !== gid),
                              )
                            }
                            className="hover:text-white"
                            aria-label={`Remove ${group ? group.name : gid}`}
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </span>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-xs text-bambu-gray/60 italic">
                    {t('inventory.noGroupRestrictions', 'No group restrictions')}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={() => {
                  setGroupSearch('');
                  setAccessModalTab('groups');
                }}
                className="w-full px-3 py-1.5 bg-bambu-dark-secondary hover:bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded-lg text-xs text-white flex items-center justify-center gap-1.5 transition-colors"
              >
                <Users className="w-3.5 h-3.5 text-bambu-green" />
                {t('inventory.selectGroupsButton', 'Select Groups / Courses...')}
              </button>
            </div>

            {/* Allowed Users */}
            <div className="bg-bambu-dark border border-bambu-dark-tertiary rounded-lg p-2.5 flex flex-col justify-between gap-2">
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-xs font-medium text-bambu-gray flex items-center gap-1.5">
                    <UserPlus className="w-3.5 h-3.5 text-blue-400" />
                    {t('inventory.allowedUsers', 'Allowed Users')}
                  </label>
                  {formData.allowed_user_ids.length > 0 && (
                    <span className="text-[11px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400 font-medium">
                      {formData.allowed_user_ids.length}
                    </span>
                  )}
                </div>
                {formData.allowed_user_ids.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5 max-h-24 overflow-y-auto py-0.5">
                    {formData.allowed_user_ids.map((uid) => {
                      const user = accessUsers.find((u) => u.id === uid);
                      return (
                        <span
                          key={uid}
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-blue-500/15 text-blue-400 border border-blue-500/30"
                        >
                          {user ? user.username : `User #${uid}`}
                          <button
                            type="button"
                            onClick={() =>
                              updateField(
                                'allowed_user_ids',
                                formData.allowed_user_ids.filter((id) => id !== uid),
                              )
                            }
                            className="hover:text-white"
                            aria-label={`Remove ${user ? user.username : uid}`}
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </span>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-xs text-bambu-gray/60 italic">
                    {t('inventory.noUserRestrictions', 'No user restrictions')}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={() => {
                  setUserSearch('');
                  setAccessModalTab('users');
                }}
                className="w-full px-3 py-1.5 bg-bambu-dark-secondary hover:bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded-lg text-xs text-white flex items-center justify-center gap-1.5 transition-colors"
              >
                <UserPlus className="w-3.5 h-3.5 text-blue-400" />
                {t('inventory.selectUsersButton', 'Select Users...')}
              </button>
            </div>
          </div>

          {/* Popup Modal for selecting Allowed Groups / Users */}
          {accessModalTab !== null && (
            <div
              className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
              onClick={() => setAccessModalTab(null)}
            >
              <div
                className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl w-full max-w-md flex flex-col max-h-[80vh] shadow-2xl overflow-hidden"
                onClick={(e) => e.stopPropagation()}
              >
                {/* Modal Header */}
                <div className="flex items-center justify-between px-4 py-3 border-b border-bambu-dark-tertiary">
                  <div className="flex items-center gap-2">
                    <Shield className="w-4 h-4 text-bambu-green" />
                    <h4 className="text-sm font-semibold text-white">
                      {t('inventory.accessControlModalTitle', 'Spool Access Control')}
                    </h4>
                  </div>
                  <button
                    type="button"
                    onClick={() => setAccessModalTab(null)}
                    className="text-bambu-gray hover:text-white transition-colors"
                    aria-label={t('common.close', 'Close')}
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>

                {/* Tabs to switch between Groups and Users inside the popup modal */}
                <div className="flex border-b border-bambu-dark-tertiary bg-bambu-dark/40">
                  <button
                    type="button"
                    onClick={() => setAccessModalTab('groups')}
                    className={`flex-1 py-2.5 px-3 text-xs font-medium border-b-2 transition-colors flex items-center justify-center gap-1.5 ${
                      accessModalTab === 'groups'
                        ? 'border-bambu-green text-bambu-green bg-bambu-green/5'
                        : 'border-transparent text-bambu-gray hover:text-white'
                    }`}
                  >
                    <Users className="w-3.5 h-3.5" />
                    {t('inventory.allowedGroups', 'Allowed Groups / Courses')}
                    {formData.allowed_group_ids.length > 0 && (
                      <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-bambu-green/20 text-bambu-green">
                        {formData.allowed_group_ids.length}
                      </span>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => setAccessModalTab('users')}
                    className={`flex-1 py-2.5 px-3 text-xs font-medium border-b-2 transition-colors flex items-center justify-center gap-1.5 ${
                      accessModalTab === 'users'
                        ? 'border-blue-400 text-blue-400 bg-blue-500/5'
                        : 'border-transparent text-bambu-gray hover:text-white'
                    }`}
                  >
                    <UserPlus className="w-3.5 h-3.5" />
                    {t('inventory.allowedUsers', 'Allowed Users')}
                    {formData.allowed_user_ids.length > 0 && (
                      <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-blue-500/20 text-blue-400">
                        {formData.allowed_user_ids.length}
                      </span>
                    )}
                  </button>
                </div>

                {/* Search + Quick Actions */}
                {accessModalTab === 'groups' ? (
                  <>
                    <div className="p-3 border-b border-bambu-dark-tertiary space-y-2">
                      <div className="relative">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
                        <input
                          type="text"
                          autoFocus
                          value={groupSearch}
                          onChange={(e) => setGroupSearch(e.target.value)}
                          placeholder={t('inventory.selectAllowedGroups', 'Search groups to allow...')}
                          className="w-full pl-9 pr-8 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
                        />
                        {groupSearch && (
                          <button
                            type="button"
                            onClick={() => setGroupSearch('')}
                            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-bambu-gray hover:text-white"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                      <div className="flex items-center justify-between text-xs text-bambu-gray">
                        <span>
                          {t('inventory.selectedCount', '{{count}} selected', {
                            count: formData.allowed_group_ids.length,
                          })}
                        </span>
                        <div className="flex items-center gap-3">
                          <button
                            type="button"
                            onClick={() => {
                              const matchingIds = accessGroups
                                .filter((g) =>
                                  g.name.toLowerCase().includes(groupSearch.trim().toLowerCase()),
                                )
                                .map((g) => g.id);
                              const merged = Array.from(
                                new Set([...formData.allowed_group_ids, ...matchingIds]),
                              );
                              updateField('allowed_group_ids', merged);
                            }}
                            className="text-bambu-green hover:underline"
                          >
                            {t('common.selectAll', 'Select All')}
                          </button>
                          {formData.allowed_group_ids.length > 0 && (
                            <button
                              type="button"
                              onClick={() => updateField('allowed_group_ids', [])}
                              className="text-bambu-gray hover:text-white hover:underline"
                            >
                              {t('common.clear', 'Clear')}
                            </button>
                          )}
                        </div>
                      </div>
                    </div>

                    <div className="flex-1 overflow-y-auto p-2 space-y-1 max-h-72">
                      {accessGroups
                        .filter((g) =>
                          g.name.toLowerCase().includes(groupSearch.trim().toLowerCase()),
                        )
                        .map((g) => {
                          const isSelected = formData.allowed_group_ids.includes(g.id);
                          return (
                            <button
                              key={g.id}
                              type="button"
                              onClick={() => {
                                const next = isSelected
                                  ? formData.allowed_group_ids.filter((id) => id !== g.id)
                                  : [...formData.allowed_group_ids, g.id];
                                updateField('allowed_group_ids', next);
                              }}
                              className={`w-full px-3 py-2 rounded-lg text-left text-sm flex items-center justify-between transition-colors ${
                                isSelected
                                  ? 'bg-bambu-green/15 text-bambu-green border border-bambu-green/30'
                                  : 'text-white hover:bg-bambu-dark-tertiary border border-transparent'
                              }`}
                            >
                              <span className="truncate">{g.name}</span>
                              <span
                                className={`w-4 h-4 rounded flex items-center justify-center border ${
                                  isSelected
                                    ? 'bg-bambu-green border-bambu-green text-black'
                                    : 'border-bambu-gray/50'
                                }`}
                              >
                                {isSelected && <Check className="w-3 h-3 stroke-[3]" />}
                              </span>
                            </button>
                          );
                        })}
                      {accessGroups.filter((g) =>
                        g.name.toLowerCase().includes(groupSearch.trim().toLowerCase()),
                      ).length === 0 && (
                        <div className="py-6 text-center text-xs text-bambu-gray">
                          {t('inventory.noResults', 'No matches found')}
                        </div>
                      )}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="p-3 border-b border-bambu-dark-tertiary space-y-2">
                      <div className="relative">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
                        <input
                          type="text"
                          autoFocus
                          value={userSearch}
                          onChange={(e) => setUserSearch(e.target.value)}
                          placeholder={t('inventory.selectAllowedUsers', 'Search users to allow...')}
                          className="w-full pl-9 pr-8 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
                        />
                        {userSearch && (
                          <button
                            type="button"
                            onClick={() => setUserSearch('')}
                            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-bambu-gray hover:text-white"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                      <div className="flex items-center justify-between text-xs text-bambu-gray">
                        <span>
                          {t('inventory.selectedCount', '{{count}} selected', {
                            count: formData.allowed_user_ids.length,
                          })}
                        </span>
                        <div className="flex items-center gap-3">
                          <button
                            type="button"
                            onClick={() => {
                              const matchingIds = accessUsers
                                .filter((u) =>
                                  u.username.toLowerCase().includes(userSearch.trim().toLowerCase()),
                                )
                                .map((u) => u.id);
                              const merged = Array.from(
                                new Set([...formData.allowed_user_ids, ...matchingIds]),
                              );
                              updateField('allowed_user_ids', merged);
                            }}
                            className="text-blue-400 hover:underline"
                          >
                            {t('common.selectAll', 'Select All')}
                          </button>
                          {formData.allowed_user_ids.length > 0 && (
                            <button
                              type="button"
                              onClick={() => updateField('allowed_user_ids', [])}
                              className="text-bambu-gray hover:text-white hover:underline"
                            >
                              {t('common.clear', 'Clear')}
                            </button>
                          )}
                        </div>
                      </div>
                    </div>

                    <div className="flex-1 overflow-y-auto p-2 space-y-1 max-h-72">
                      {accessUsers
                        .filter((u) =>
                          u.username.toLowerCase().includes(userSearch.trim().toLowerCase()),
                        )
                        .map((u) => {
                          const isSelected = formData.allowed_user_ids.includes(u.id);
                          return (
                            <button
                              key={u.id}
                              type="button"
                              onClick={() => {
                                const next = isSelected
                                  ? formData.allowed_user_ids.filter((id) => id !== u.id)
                                  : [...formData.allowed_user_ids, u.id];
                                updateField('allowed_user_ids', next);
                              }}
                              className={`w-full px-3 py-2 rounded-lg text-left text-sm flex items-center justify-between transition-colors ${
                                isSelected
                                  ? 'bg-blue-500/15 text-blue-400 border border-blue-500/30'
                                  : 'text-white hover:bg-bambu-dark-tertiary border border-transparent'
                              }`}
                            >
                              <span className="truncate">{u.username}</span>
                              <span
                                className={`w-4 h-4 rounded flex items-center justify-center border ${
                                  isSelected
                                    ? 'bg-blue-500 border-blue-500 text-white'
                                    : 'border-bambu-gray/50'
                                }`}
                              >
                                {isSelected && <Check className="w-3 h-3 stroke-[3]" />}
                              </span>
                            </button>
                          );
                        })}
                      {accessUsers.filter((u) =>
                        u.username.toLowerCase().includes(userSearch.trim().toLowerCase()),
                      ).length === 0 && (
                        <div className="py-6 text-center text-xs text-bambu-gray">
                          {t('inventory.noResults', 'No matches found')}
                        </div>
                      )}
                    </div>
                  </>
                )}

                {/* Modal Footer */}
                <div className="px-4 py-3 border-t border-bambu-dark-tertiary flex items-center justify-between bg-bambu-dark/40">
                  <span className="text-xs text-bambu-gray">
                    {formData.allowed_group_ids.length === 0 && formData.allowed_user_ids.length === 0
                      ? t('inventory.unrestrictedSummary', 'Unrestricted (everyone can use)')
                      : t(
                          'inventory.restrictedSummary',
                          '{{groups}} group(s), {{users}} user(s) allowed',
                          {
                            groups: formData.allowed_group_ids.length,
                            users: formData.allowed_user_ids.length,
                          },
                        )}
                  </span>
                  <button
                    type="button"
                    onClick={() => setAccessModalTab(null)}
                    className="px-4 py-1.5 rounded-lg bg-bambu-green text-black font-medium text-xs hover:bg-bambu-green/90 transition-colors"
                  >
                    {t('common.done', 'Done')}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

    </div>
  );
}
