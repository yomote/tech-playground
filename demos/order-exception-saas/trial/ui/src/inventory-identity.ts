import Keycloak from 'keycloak-js';

// This browser client exists only in the isolated Tailnet HTTPS OE-02 trial.
// Keycloak JS owns the authorization-code callback, PKCE and token refresh.
export const inventoryIdentity = new Keycloak({
  url: `${window.location.origin}/trial/auth`,
  realm: 'tp-population-20261006',
  clientId: 'oe02-user-entry-trial',
});

let initialization: Promise<boolean> | undefined;

export function initializeInventoryIdentity(): Promise<boolean> {
  initialization ??= inventoryIdentity.init({
    onLoad: 'check-sso',
    // The isolated Keycloak client allows this exact redirect URI only.
    redirectUri: `${window.location.origin}/trial/`,
    pkceMethod: 'S256',
    checkLoginIframe: false,
  });
  return initialization;
}

export async function inventoryAccessToken(): Promise<string> {
  if (!inventoryIdentity.authenticated) {
    throw new Error('Keycloakで担当者としてログインしてください。');
  }
  try {
    await inventoryIdentity.updateToken(30);
  } catch {
    inventoryIdentity.clearToken();
    throw new Error('ログインを更新できません。Keycloakの接続を確認して再ログインしてください。');
  }
  if (!inventoryIdentity.token) {
    throw new Error('ログイン情報を確認できません。再ログインしてください。');
  }
  return inventoryIdentity.token;
}
