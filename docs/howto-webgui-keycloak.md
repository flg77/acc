# acc-webgui with Keycloak (OIDC) — enterprise SSO + RBAC

acc-webgui is the **interaction plane** of ADR 025 (the browser surface humans
use to run a deployed corpus). This guide wires it to **Keycloak** for login
and maps Keycloak **realm roles / groups** to ACC's three tiers
(viewer / operator / publisher), so RBAC is driven by your IdP — not by ACC.

Per ADR 025 there is **one identity system**. Keycloak is it: it can also back
OpenShift OAuth as an OIDC IdP, so the console plugin (020 WS-B), the webgui,
and the cluster all authenticate the same humans. ACC adds **zero** auth of its
own — it only *maps* the IdP's groups to capability tiers.

## Two topologies (both "Keycloak integrated")

| Path | Who logs in | Mechanism | webgui mode |
|---|---|---|---|
| **Browser (humans)** | a user in a browser | an **oauth2-proxy sidecar** does the Keycloak OIDC auth-code flow, then forwards identity + groups headers | `oauth-proxy` (reads `X-Forwarded-Groups`) |
| **API / CLI (programmatic)** | a script with a Keycloak token | the webgui validates the **bearer token** against the Keycloak realm JWKS | `oidc` (validates `aud`/`azp` + realm roles) |

Both map the same Keycloak groups/roles → tiers. Browser users never handle
tokens (the proxy does); the SPA only ever talks to the proxy. The webgui pod
port is **never** exposed directly (NetworkPolicy: Route → proxy only) — 025 §5.

## Tier mapping (Keycloak group/role → ACC tier)

| ACC tier | Capability | Default Keycloak group/role (027) |
|---|---|---|
| `viewer` | read + traces | everyone authenticated (no match) |
| `operator` | + infuse / prompt / oversight / test-llm | `acc-operators` |
| `publisher` | + publish signed packs (020 WS-C3) | `acc-publishers` |

Ladder: `publisher ⊇ operator ⊇ viewer`. The mapping is config, not code — set
`ACC_WEBGUI_GROUP_MAPPINGS` (the names come from lab-gitops backlog 006's
`group_vars/rbac.yml`, the single adjustable source).

The webgui reads groups from **all three** Keycloak shapes: a `groups` mapper
(`/acc-operators` paths are slash-normalised), `realm_access.roles` (realm
roles), and `resource_access.<client>.roles` (client roles for the configured
audience).

## webgui env reference

```
ACC_WEBGUI_AUTH_MODE=oauth-proxy      # browser path (oauth2-proxy sidecar)
#                    =oidc            # API path (validate Keycloak bearer tokens)
ACC_WEBGUI_OIDC_ISSUER=https://<keycloak>/realms/<realm>
ACC_WEBGUI_OIDC_AUDIENCE=acc-webgui   # the Keycloak client_id (aud/azp check)
ACC_WEBGUI_OIDC_GROUPS_CLAIM=groups   # token claim carrying group names
ACC_WEBGUI_GROUP_MAPPINGS=operator=acc-operators;publisher=acc-publishers
```

## Keycloak setup (realm, client, group mapper) — the working recipe

1. **Realm**: use your existing realm (or `acc`).
2. **Client** `acc-webgui`: confidential, standard flow on; valid redirect URIs
   = the webgui Route + `/oauth2/callback`; copy the client secret.
3. **Groups** `acc-operators`, `acc-publishers`; assign users.
4. **Client scope / mapper**: add a **Group Membership** mapper (token claim
   name `groups`, "Full group path" OFF so names are bare) OR a realm-role
   mapper — the webgui reads either.

## Deploy (gitops manifest — works today, ahead of the operator `spec.webgui`)

The Keycloak client secret lives in a Secret (never in the manifest):

```yaml
apiVersion: v1
kind: Secret
metadata: { name: acc-webgui-keycloak, namespace: acc-system }
stringData:
  client-secret: "<from Keycloak>"
  cookie-secret: "<openssl rand -base64 32>"
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: acc-webgui, namespace: acc-system }
spec:
  replicas: 1
  selector: { matchLabels: { app: acc-webgui } }
  template:
    metadata: { labels: { app: acc-webgui } }
    spec:
      containers:
        - name: webgui
          image: quay.io/flg77/acc_images:acc-webgui-<ver>
          args: ["acc-webgui", "--host", "127.0.0.1", "--port", "8080"]  # proxy is sole ingress
          env:
            - { name: ACC_WEBGUI_AUTH_MODE, value: oauth-proxy }
            - { name: ACC_WEBGUI_OIDC_GROUPS_CLAIM, value: groups }
            - { name: ACC_WEBGUI_GROUP_MAPPINGS, value: "operator=acc-operators;publisher=acc-publishers" }
        - name: oauth2-proxy
          image: quay.io/oauth2-proxy/oauth2-proxy:v7.6.0
          args:
            - --provider=keycloak-oidc
            - --oidc-issuer-url=https://<keycloak>/realms/<realm>
            - --client-id=acc-webgui
            - --client-secret=$(CLIENT_SECRET)
            - --cookie-secret=$(COOKIE_SECRET)
            - --email-domain=*
            - --upstream=http://127.0.0.1:8080
            - --pass-access-token=true
            - --pass-user-headers=true
            - --set-xauthrequest=true
            - --scope=openid email groups
            - --allowed-group=acc-operators        # optional: gate at the proxy
            - --allowed-group=acc-publishers
            - --http-address=0.0.0.0:4180
          env:
            - { name: CLIENT_SECRET, valueFrom: { secretKeyRef: { name: acc-webgui-keycloak, key: client-secret } } }
            - { name: COOKIE_SECRET, valueFrom: { secretKeyRef: { name: acc-webgui-keycloak, key: cookie-secret } } }
          ports: [{ containerPort: 4180 }]
---
apiVersion: v1
kind: Service
metadata: { name: acc-webgui, namespace: acc-system }
spec:
  selector: { app: acc-webgui }
  ports: [{ name: http, port: 4180, targetPort: 4180 }]
---
apiVersion: route.openshift.io/v1
kind: Route
metadata: { name: acc-webgui, namespace: acc-system }
spec:
  to: { kind: Service, name: acc-webgui }
  port: { targetPort: 4180 }
  tls: { termination: edge, insecureEdgeTerminationPolicy: Redirect }
```

> oauth2-proxy forwards the group list; acc-webgui (`oauth-proxy` mode) maps it
> to a tier via `ACC_WEBGUI_GROUP_MAPPINGS`. `--allowed-group` at the proxy is
> belt-and-suspenders; the tier mapping is the authoritative RBAC.

## OpenShift-OAuth alternative

If you'd rather federate through **OpenShift OAuth** (which itself trusts
Keycloak as an OIDC IdP), swap the oauth2-proxy sidecar for `openshift/oauth-proxy`
with `--pass-groups`; acc-webgui's `oauth-proxy` mode reads the same
`X-Forwarded-Groups`. Same tier mapping, same ADR-025 posture — pick based on
whether you want the webgui to hit Keycloak directly or via OpenShift's OAuth.

## Operator automation (staged — operator 0.2.0)

`spec.webgui` on AgentCorpus will render exactly the above (Deployment +
oauth2-proxy sidecar + Service + Route) from a small spec
(`enabled`, `keycloak.{issuerURL,clientID,clientSecretRef,groupsClaim}`,
`groupMappings`, `replicas`), defaulted on in rhoai mode (023 §4c). A new
corpus-level `SubReconciler` (`internal/reconcilers/ui/webgui.go`) builds them;
the Route is created unstructured + discovery-gated (mirrors the rhoai
dashboard CRs). Until it lands, the manifest above is the gitops recipe.

## References
- ADR 025 (federated three-plane model — identity plane), 023 (webgui default + RBAC),
  027 + lab-gitops backlog 006 (group names → tiers), 020 WS-C3 (publish → publisher tier)
- `acc/webgui/auth.py` (MODE_OIDC / MODE_OAUTH_PROXY, `role_from_claims`, `_audience_ok`)
- Keycloak OIDC; oauth2-proxy keycloak-oidc provider; openshift/oauth-proxy
