/* eslint-env node */
/*
 * OpenShift dynamic console plugin webpack config (proposal 035, PR-1).
 *
 * Faithful to openshift/console-plugin-template, adapted to CommonJS +
 * the npm toolchain this repo already uses (webgui/ is npm, and the CI
 * parity gate runs in pytest WITHOUT Node). The plugin's federation
 * manifest (name / exposedModules / dependencies) is read from the
 * `consolePlugin` block in package.json by ConsoleRemotePlugin.
 */
const path = require('path');
const { ConsoleRemotePlugin } = require('@openshift-console/dynamic-plugin-sdk-webpack');
const CopyWebpackPlugin = require('copy-webpack-plugin');
const ForkTsCheckerWebpackPlugin = require('fork-ts-checker-webpack-plugin');

const isProd = process.env.NODE_ENV === 'production';

/** @type {import('webpack').Configuration} */
module.exports = (env, argv) => {
  const mode = argv && argv.mode ? argv.mode : isProd ? 'production' : 'development';
  const production = mode === 'production';

  return {
    mode,
    // ConsoleRemotePlugin injects the real (module-federation) entry; this
    // empty context just keeps webpack's default `./src` resolution sane.
    context: path.resolve(__dirname, 'src'),
    entry: {},
    output: {
      path: path.resolve(__dirname, 'dist'),
      filename: production ? '[name]-bundle-[hash].min.js' : '[name]-bundle.js',
      chunkFilename: production ? '[name]-chunk-[chunkhash].min.js' : '[name]-chunk.js',
      // NOTE: ConsoleRemotePlugin sets publicPath to /api/plugins/<plugin-name>/
      // (the console's plugin asset route) automatically; do not set it here.
    },
    resolve: {
      extensions: ['.ts', '.tsx', '.js', '.jsx'],
    },
    module: {
      rules: [
        {
          test: /\.(jsx?|tsx?)$/,
          exclude: /node_modules/,
          use: {
            loader: 'swc-loader',
            options: {
              jsc: {
                parser: { syntax: 'typescript', tsx: true },
                transform: { react: { runtime: 'automatic' } },
                target: 'es2020',
              },
            },
          },
        },
        {
          test: /\.css$/,
          use: ['style-loader', 'css-loader'],
        },
        {
          test: /\.(png|jpe?g|gif|svg|woff2?|ttf|eot)$/,
          type: 'asset/resource',
          generator: { filename: 'assets/[name][ext]' },
        },
      ],
    },
    plugins: [
      // Reads package.json's `consolePlugin` block → emits plugin-manifest.json
      // + the module-federation container. This is what makes it a console plugin.
      new ConsoleRemotePlugin(),
      new ForkTsCheckerWebpackPlugin({
        typescript: { configFile: path.resolve(__dirname, 'tsconfig.json') },
      }),
      new CopyWebpackPlugin({
        patterns: [{ from: 'locales', to: 'locales', noErrorOnMissing: true }],
      }),
    ],
    devtool: production ? 'source-map' : 'eval-source-map',
    optimization: {
      chunkIds: production ? 'deterministic' : 'named',
      minimize: production,
    },
    devServer: {
      static: path.resolve(__dirname, 'dist'),
      port: 9001,
      // Console (running elsewhere) loads the federated bundle cross-origin.
      headers: { 'Access-Control-Allow-Origin': '*' },
      allowedHosts: 'all',
    },
  };
};
