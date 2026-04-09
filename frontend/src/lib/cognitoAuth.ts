import {
  AuthenticationDetails,
  CognitoUser,
  CognitoUserAttribute,
  CognitoUserPool,
} from "amazon-cognito-identity-js";

const userPoolId = import.meta.env.VITE_COGNITO_USER_POOL_ID;
const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID;

if (!userPoolId || !clientId) {
  throw new Error("Missing VITE_COGNITO_USER_POOL_ID or VITE_COGNITO_CLIENT_ID.");
}

const userPool = new CognitoUserPool({
  UserPoolId: userPoolId,
  ClientId: clientId,
});

const ACCESS_TOKEN_KEY = "cognitoAccessToken";
const ID_TOKEN_KEY = "cognitoIdToken";
const USERNAME_KEY = "cognitoUsername";

export function signUpWithCognito(params: {
  username: string;
  email: string;
  password: string;
  alpacaKey: string;
  alpacaSecret: string;
}) {
  const { username, email, password, alpacaKey, alpacaSecret } = params;
  const attributes = [
    new CognitoUserAttribute({ Name: "email", Value: email }),
    new CognitoUserAttribute({ Name: "custom:alpaca_key", Value: alpacaKey }),
    new CognitoUserAttribute({ Name: "custom:alpaca_secret", Value: alpacaSecret }),
  ];

  return new Promise<void>((resolve, reject) => {
    userPool.signUp(username, password, attributes, [], (error) => {
      if (error) {
        reject(error);
        return;
      }
      resolve();
    });
  });
}

export function signInWithCognito(username: string, password: string) {
  const authDetails = new AuthenticationDetails({
    Username: username,
    Password: password,
  });
  const cognitoUser = new CognitoUser({
    Username: username,
    Pool: userPool,
  });

  return new Promise<void>((resolve, reject) => {
    cognitoUser.authenticateUser(authDetails, {
      onSuccess: (session) => {
        localStorage.setItem(ACCESS_TOKEN_KEY, session.getAccessToken().getJwtToken());
        localStorage.setItem(ID_TOKEN_KEY, session.getIdToken().getJwtToken());
        localStorage.setItem(USERNAME_KEY, username);
        resolve();
      },
      onFailure: (error) => {
        reject(error);
      },
    });
  });
}

export function confirmSignUpWithCognito(username: string, code: string) {
  const cognitoUser = new CognitoUser({
    Username: username,
    Pool: userPool,
  });

  return new Promise<void>((resolve, reject) => {
    cognitoUser.confirmRegistration(code, true, (error) => {
      if (error) {
        reject(error);
        return;
      }
      resolve();
    });
  });
}

export function getAccessToken() {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getIdToken() {
  return localStorage.getItem(ID_TOKEN_KEY);
}

export function getStoredUsername() {
  return localStorage.getItem(USERNAME_KEY);
}

export function isAuthenticated() {
  return Boolean(getAccessToken() && getIdToken());
}

export function signOut() {
  const username = getStoredUsername();
  if (username) {
    const user = new CognitoUser({ Username: username, Pool: userPool });
    user.signOut();
  }

  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(ID_TOKEN_KEY);
  localStorage.removeItem(USERNAME_KEY);
}
