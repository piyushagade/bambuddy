import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Loader2, Users as UsersIcon } from 'lucide-react';
import { Button } from './Button';
import { LdapUserPicker } from './LdapUserPicker';
import { PopupModal } from './PopupModal';
import { UserExpirationField } from './UserExpirationField';
import type { Group, UserCreate, UserResponse } from '../api/client';

interface AdvancedAuthFormData extends UserCreate {
  group_ids: number[];
  confirmPassword: string;
  email?: string;
}

interface CreateUserAdvancedAuthModalProps {
  formData: AdvancedAuthFormData;
  setFormData: (data: AdvancedAuthFormData) => void;
  groups: Group[];
  onClose: () => void;
  onCreate: () => void;
  isCreating: boolean;
  isCreateButtonDisabled: boolean;
  // When LDAP is enabled in settings, the modal shows a "LDAP" tab beside
  // "Local"; the picker handles its own provision call and reports success
  // back through onLdapProvisioned.
  ldapEnabled?: boolean;
  onLdapProvisioned?: (user: UserResponse) => void;
}

type Tab = 'local' | 'ldap';

export function CreateUserAdvancedAuthModal({
  formData,
  setFormData,
  groups,
  onClose,
  onCreate,
  isCreating,
  isCreateButtonDisabled,
  ldapEnabled = false,
  onLdapProvisioned,
}: CreateUserAdvancedAuthModalProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>('local');

  const toggleGroup = (groupId: number) => {
    setFormData({
      ...formData,
      group_ids: formData.group_ids.includes(groupId)
        ? formData.group_ids.filter(id => id !== groupId)
        : [...formData.group_ids, groupId],
    });
  };

  return (
    <PopupModal
      onClose={onClose}
      maxWidth="md"
      icon={<UsersIcon className="w-5 h-5 text-bambu-green" />}
      title={t('users.modal.createUser')}
      subtitle={t('users.modal.advancedAuthSubtitle') || 'with Advanced Authentication'}
      footer={
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            {t('users.modal.cancel')}
          </Button>
          {tab === 'local' && (
            <Button onClick={onCreate} disabled={isCreateButtonDisabled}>
              {isCreating ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {t('users.modal.creating')}
                </>
              ) : (
                <>
                  <Plus className="w-4 h-4" />
                  {t('users.modal.createUser')}
                </>
              )}
            </Button>
          )}
        </div>
      }
    >
      {ldapEnabled && (
        <div
          className="mb-4 flex items-center gap-1 p-1 bg-bambu-dark-secondary rounded-lg"
          role="tablist"
          aria-label={t('users.modal.tabsAriaLabel')}
        >
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'local'}
            onClick={() => setTab('local')}
            className={`flex-1 px-3 py-2 text-sm rounded-md transition-colors ${
              tab === 'local'
                ? 'bg-bambu-green/15 text-bambu-green'
                : 'text-bambu-gray hover:text-white'
            }`}
          >
            {t('users.modal.localTab')}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'ldap'}
            onClick={() => setTab('ldap')}
            className={`flex-1 px-3 py-2 text-sm rounded-md transition-colors ${
              tab === 'ldap'
                ? 'bg-bambu-green/15 text-bambu-green'
                : 'text-bambu-gray hover:text-white'
            }`}
          >
            {t('users.modal.ldapTab')}
          </button>
        </div>
      )}

      {tab === 'ldap' && ldapEnabled ? (
        <LdapUserPicker
          onSuccess={(user) => {
            onLdapProvisioned?.(user);
          }}
        />
      ) : (
        <div className="space-y-4">
          {/* Username Field */}
          <div>
            <label className="block text-sm font-medium text-white mb-2">
              {t('users.form.username')} <span className="text-red-600 dark:text-red-400">*</span>
            </label>
            <input
              type="text"
              value={formData.username}
              onChange={(e) => setFormData({ ...formData, username: e.target.value })}
              className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
              placeholder={t('users.form.usernamePlaceholder')}
              autoComplete="username"
              required
            />
          </div>

          {/* First Name & Last Name */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-white mb-2">
                First Name
              </label>
              <input
                type="text"
                value={formData.first_name || ''}
                onChange={(e) => setFormData({ ...formData, first_name: e.target.value })}
                className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                placeholder="First name"
                autoComplete="given-name"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-white mb-2">
                Last Name
              </label>
              <input
                type="text"
                value={formData.last_name || ''}
                onChange={(e) => setFormData({ ...formData, last_name: e.target.value })}
                className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                placeholder="Last name"
                autoComplete="family-name"
              />
            </div>
          </div>

          {/* Email Field */}
          <div>
            <label className="block text-sm font-medium text-white mb-2">
              {t('users.form.email') || 'Email'} <span className="text-red-600 dark:text-red-400">*</span>
            </label>
            <input
              type="email"
              value={formData.email}
              onChange={(e) => setFormData({ ...formData, email: e.target.value })}
              className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
              placeholder={t('users.form.emailPlaceholder') || 'user@example.com'}
              required
            />
          </div>

          {/* Info box about auto-generated password */}
          <div className="bg-bambu-dark-secondary/50 border border-bambu-green/20 rounded-lg p-3">
            <p className="text-sm text-bambu-gray">
              {t('users.form.autoGeneratedPassword') || 'A secure password will be automatically generated and emailed to the user.'}
            </p>
          </div>

          {/* Termination / Expiration Date */}
          <UserExpirationField
            value={formData.expires_at || ''}
            onChange={(expires_at) => setFormData({ ...formData, expires_at })}
          />

          {/* Groups Field */}
          <div>
            <label className="block text-sm font-medium text-white mb-2">
              {t('users.form.groups')}
            </label>
            <div className="space-y-2 max-h-40 overflow-y-auto p-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg">
              {groups.map(group => (
                <label
                  key={group.id}
                  className="flex items-center gap-3 px-2 py-1.5 rounded hover:bg-bambu-dark-tertiary cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={formData.group_ids.includes(group.id)}
                    onChange={() => toggleGroup(group.id)}
                    className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green focus:ring-offset-0 bg-bambu-dark"
                  />
                  <span className="text-sm text-white">{group.name}</span>
                  {group.is_system && (
                    <span className="text-xs text-yellow-700 dark:text-yellow-400">({t('users.system')})</span>
                  )}
                </label>
              ))}
              {groups.length === 0 && (
                <p className="text-sm text-bambu-gray">{t('users.noGroupsAvailable')}</p>
              )}
            </div>
          </div>

          {/* Project Waiver Checkbox */}
          <label className="flex items-start gap-3 p-3 rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary cursor-pointer">
            <input
              type="checkbox"
              checked={Boolean(formData.project_waiver)}
              onChange={(e) => setFormData({ ...formData, project_waiver: e.target.checked })}
              className="mt-0.5 w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green focus:ring-offset-0 bg-bambu-dark"
            />
            <div>
              <span className="text-sm font-medium text-white block">
                Waive Project Requirement
              </span>
              <span className="text-xs text-bambu-gray block mt-0.5">
                Allow this user to queue and print models without selecting a project, even if their group requires one.
              </span>
            </div>
          </label>
        </div>
      )}
    </PopupModal>
  );
}
