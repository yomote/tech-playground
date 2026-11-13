import Keycloak from 'keycloak-js';

// This browser client exists only in the isolated Tailnet HTTPS OE-02 trial.
// Keycloak JS owns the authorization-code callback, PKCE and token refresh.
export const inventoryIdentity = new Keycloak({
  url: import.meta.env.VITE_OE02_KEYCLOAK_URL || `${window.location.origin}/trial/auth`,
  realm: 'tp-population-20261006',
  clientId: import.meta.env.VITE_OE02_KEYCLOAK_CLIENT_ID || 'oe02-order18-1106',
});

let initialization: Promise<boolean> | undefined;

export function initializeInventoryIdentity(): Promise<boolean> {
  initialization ??= inventoryIdentity.init({
    // Keep the first visit anonymous. The owner gate accepts only an
    // authorization-code return, while check-sso's prompt=none returns
    // error=login_required for a new browser session.
    // The isolated Keycloak client allows this exact redirect URI only.
    redirectUri: import.meta.env.VITE_OE02_KEYCLOAK_REDIRECT_URI || `${window.location.origin}/trial-order18/`,
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
