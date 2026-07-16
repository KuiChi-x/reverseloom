const path = require('path');
const TerserPlugin = require('terser-webpack-plugin');
const pkg = require('./package.json');

module.exports = {
  target: 'node',
  mode: 'production',
  entry: './reverseloom-sandbox.js',
  output: {
    filename: 'reverseloom-sandbox.bundle.js',
    path: path.resolve(__dirname),
  },
  externals: {
    jsdom: 'commonjs jsdom',
    canvas: 'commonjs canvas',
  },
  resolve: {
    extensions: ['.js', '.json'],
  },
  optimization: {
    minimizer: [new TerserPlugin({ extractComments: false })],
  },
  plugins: [
    new (require('webpack')).BannerPlugin({
      banner: `${pkg.name} v${pkg.version} | Author: ${pkg.author}`,
    }),
  ],
};
