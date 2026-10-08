import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const taskRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const frontendRoot = path.join(taskRoot, 'frontend')
const dependencyCandidates = [
  path.join(frontendRoot, 'node_modules'),
  'D:\\ChatGPT项目\\金融AI智能体_V3.1.3\\frontend\\node_modules',
  'D:\\ChatGPT项目\\金融AI智能体\\frontend\\node_modules',
]
const sharedModules = dependencyCandidates.find((directory) =>
  existsSync(path.join(directory, 'vite', 'dist', 'node', 'index.js'))
  && existsSync(path.join(directory, '@vitejs', 'plugin-react', 'dist', 'index.js'))
  && existsSync(path.join(directory, 'react', 'index.js'))
  && existsSync(path.join(directory, 'react-dom', 'client.js')),
)

if (!sharedModules) {
  throw new Error('没有找到 Vite、React 和 React DOM。请在 frontend 目录运行 npm ci 后再启动。')
}

const viteUrl = pathToFileURL(path.join(sharedModules, 'vite', 'dist', 'node', 'index.js')).href
const reactPluginUrl = pathToFileURL(path.join(sharedModules, '@vitejs', 'plugin-react', 'dist', 'index.js')).href
const { build } = await import(viteUrl)
const { default: react } = await import(reactPluginUrl)

await build({
  configFile: false,
  root: frontendRoot,
  base: '/',
  plugins: [react()],
  resolve: {
    preserveSymlinks: true,
    alias: [
      { find: 'react', replacement: path.join(sharedModules, 'react') },
      { find: 'react-dom', replacement: path.join(sharedModules, 'react-dom') },
    ],
    dedupe: ['react', 'react-dom'],
  },
  build: {
    outDir: path.join(frontendRoot, 'dist'),
    emptyOutDir: true,
  },
  logLevel: 'info',
})
