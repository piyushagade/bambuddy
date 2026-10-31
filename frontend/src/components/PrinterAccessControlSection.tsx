import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Check, Globe, Lock, Search, Shield, UserPlus, Users, X } from 'lucide-react';
import { api } from '../api/client';

interface PrinterAccessControlSectionProps {
  allowedGroupIds: number[];
  allowedUserIds: number[];
  onChange: (allowedGroupIds: number[], allowedUserIds: number[]) => void;
}

export function PrinterAccessControlSection({
  allowedGroupIds,
  allowedUserIds,
  onChange,
}: PrinterAccessControlSectionProps) {
  const { t } = useTranslation();
  const hasRestrictions = allowedGroupIds.length > 0 || allowedUserIds.length > 0;
  const [restrictedMode, setRestrictedMode] = useState<boolean>(hasRestrictions);
  const [accessModalTab, setAccessModalTab] = useState<'groups' | 'users' | null>(null);
  const [groupSearch, setGroupSearch] = useState('');
  const [userSearch, setUserSearch] = useState('');

  const { data: accessOptions } = useQuery({
    queryKey: ['spool-access-options'],
    queryFn: api.getSpoolAccessOptions,
    staleTime: 60_000,
  });

  const accessGroups = accessOptions?.groups ?? [];
  const accessUsers = accessOptions?.users ?? [];

  const isRestricted = restrictedMode || hasRestrictions;

  const handleSelectOpenAccess = () => {
    setRestrictedMode(false);
    if (allowedGroupIds.length > 0 || allowedUserIds.length > 0) {
      onChange([], []);
    }
  };

  const handleSelectRestrictedAccess = () => {
    setRestrictedMode(true);
  };

  return (
    <div className="pt-2 border-t border-bambu-dark-tertiary space-y-2.5">
      <div className="flex items-center justify-between">
        <label className="text-sm font-medium text-white flex items-center gap-1.5">
          <Shield className="w-4 h-4 text-bambu-green" />
          {t('printers.accessControl.title', 'Printer Access Control')}
        </label>
        <div className="inline-flex rounded-lg bg-bambu-dark border border-bambu-dark-tertiary p-0.5">
          <button
            type="button"
            onClick={handleSelectOpenAccess}
            className={`px-2.5 py-1 rounded-md text-xs font-medium flex items-center gap-1 transition-colors ${
              !isRestricted
                ? 'bg-bambu-green text-black'
                : 'text-bambu-gray hover:text-white'
            }`}
          >
            <Globe className="w-3.5 h-3.5" />
            {t('printers.accessControl.openAccess', 'Open Access')}
          </button>
          <button
            type="button"
            onClick={handleSelectRestrictedAccess}
            className={`px-2.5 py-1 rounded-md text-xs font-medium flex items-center gap-1 transition-colors ${
              isRestricted
                ? 'bg-amber-500 text-black'
                : 'text-bambu-gray hover:text-white'
            }`}
          >
            <Lock className="w-3.5 h-3.5" />
            {t('printers.accessControl.restricted', 'Restricted')}
          </button>
        </div>
      </div>

      <p className="text-xs text-bambu-gray">
        {!isRestricted
          ? t(
              'printers.accessControl.openHint',
              'All users with printer permissions can view and print on this printer.',
            )
          : t(
              'printers.accessControl.restrictedHint',
              'Only selected groups (courses/cohorts), selected users, and administrators can view and use this printer.',
            )}
      </p>

      {isRestricted && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          {/* Allowed Groups */}
          <div className="bg-bambu-dark border border-bambu-dark-tertiary rounded-lg p-2.5 flex flex-col justify-between gap-2">
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-xs font-medium text-bambu-gray flex items-center gap-1.5">
                  <Users className="w-3.5 h-3.5 text-bambu-green" />
                  {t('inventory.allowedGroups', 'Allowed Groups / Courses')}
                </span>
                {allowedGroupIds.length > 0 && (
                  <span className="text-[11px] px-1.5 py-0.5 rounded bg-bambu-green/15 text-bambu-green font-medium">
                    {allowedGroupIds.length}
                  </span>
                )}
              </div>
              {allowedGroupIds.length > 0 ? (
                <div className="flex flex-wrap gap-1.5 max-h-20 overflow-y-auto py-0.5">
                  {allowedGroupIds.map((gid) => {
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
                            onChange(
                              allowedGroupIds.filter((id) => id !== gid),
                              allowedUserIds,
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
              className="w-full px-2.5 py-1.5 bg-bambu-dark-secondary hover:bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded-lg text-xs text-white flex items-center justify-center gap-1.5 transition-colors"
            >
              <Users className="w-3.5 h-3.5 text-bambu-green" />
              {t('inventory.selectGroupsButton', 'Select Groups / Courses...')}
            </button>
          </div>

          {/* Allowed Users */}
          <div className="bg-bambu-dark border border-bambu-dark-tertiary rounded-lg p-2.5 flex flex-col justify-between gap-2">
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-xs font-medium text-bambu-gray flex items-center gap-1.5">
                  <UserPlus className="w-3.5 h-3.5 text-blue-400" />
                  {t('inventory.allowedUsers', 'Allowed Users')}
                </span>
                {allowedUserIds.length > 0 && (
                  <span className="text-[11px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400 font-medium">
                    {allowedUserIds.length}
                  </span>
                )}
              </div>
              {allowedUserIds.length > 0 ? (
                <div className="flex flex-wrap gap-1.5 max-h-20 overflow-y-auto py-0.5">
                  {allowedUserIds.map((uid) => {
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
                            onChange(
                              allowedGroupIds,
                              allowedUserIds.filter((id) => id !== uid),
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
              className="w-full px-2.5 py-1.5 bg-bambu-dark-secondary hover:bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded-lg text-xs text-white flex items-center justify-center gap-1.5 transition-colors"
            >
              <UserPlus className="w-3.5 h-3.5 text-blue-400" />
              {t('inventory.selectUsersButton', 'Select Users...')}
            </button>
          </div>
        </div>
      )}

      {/* Searchable Multi-Select Modal for Allowed Groups / Users */}
      {accessModalTab !== null && (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4"
          onClick={() => setAccessModalTab(null)}
        >
          <div
            className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl w-full max-w-md flex flex-col max-h-[80vh] shadow-2xl overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-bambu-dark-tertiary">
              <div className="flex items-center gap-2">
                <Shield className="w-4 h-4 text-bambu-green" />
                <h4 className="text-sm font-semibold text-white">
                  {t('printers.accessControl.modalTitle', 'Printer Access Control')}
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
                {allowedGroupIds.length > 0 && (
                  <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-bambu-green/20 text-bambu-green">
                    {allowedGroupIds.length}
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
                {allowedUserIds.length > 0 && (
                  <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-blue-500/20 text-blue-400">
                    {allowedUserIds.length}
                  </span>
                )}
              </button>
            </div>

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
                        count: allowedGroupIds.length,
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
                          const merged = Array.from(new Set([...allowedGroupIds, ...matchingIds]));
                          onChange(merged, allowedUserIds);
                        }}
                        className="text-bambu-green hover:underline"
                      >
                        {t('common.selectAll', 'Select All')}
                      </button>
                      {allowedGroupIds.length > 0 && (
                        <button
                          type="button"
                          onClick={() => onChange([], allowedUserIds)}
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
                      const isSelected = allowedGroupIds.includes(g.id);
                      return (
                        <button
                          key={g.id}
                          type="button"
                          onClick={() => {
                            const next = isSelected
                              ? allowedGroupIds.filter((id) => id !== g.id)
                              : [...allowedGroupIds, g.id];
                            onChange(next, allowedUserIds);
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
                        count: allowedUserIds.length,
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
                          const merged = Array.from(new Set([...allowedUserIds, ...matchingIds]));
                          onChange(allowedGroupIds, merged);
                        }}
                        className="text-blue-400 hover:underline"
                      >
                        {t('common.selectAll', 'Select All')}
                      </button>
                      {allowedUserIds.length > 0 && (
                        <button
                          type="button"
                          onClick={() => onChange(allowedGroupIds, [])}
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
                      const isSelected = allowedUserIds.includes(u.id);
                      return (
                        <button
                          key={u.id}
                          type="button"
                          onClick={() => {
                            const next = isSelected
                              ? allowedUserIds.filter((id) => id !== u.id)
                              : [...allowedUserIds, u.id];
                            onChange(allowedGroupIds, next);
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

            <div className="px-4 py-3 border-t border-bambu-dark-tertiary flex items-center justify-between bg-bambu-dark/40">
              <span className="text-xs text-bambu-gray">
                {allowedGroupIds.length === 0 && allowedUserIds.length === 0
                  ? t('inventory.unrestrictedSummary', 'Unrestricted (everyone can use)')
                  : t(
                      'inventory.restrictedSummary',
                      '{{groups}} group(s), {{users}} user(s) allowed',
                      {
                        groups: allowedGroupIds.length,
                        users: allowedUserIds.length,
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
  );
}
