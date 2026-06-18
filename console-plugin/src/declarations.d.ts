/*
 * Minimal ambient typings for the console-provided shared modules we consume but
 * do not ship our own @types for (proposal 035, PR-2).
 *
 * react-router-dom is a console singleton shared module (allowFallback: false in
 * the SDK's shared-modules list) at v5; we use only useParams to resolve the
 * :name / :ns route params on resource/details pages. Declaring just that hook
 * keeps the plugin's type surface honest without pulling @types/react-router-dom
 * (and its react-router peer types) into the build.
 */
declare module 'react-router-dom' {
  export function useParams<
    P extends { [K in keyof P]?: string } = Record<string, string | undefined>,
  >(): P;
}
