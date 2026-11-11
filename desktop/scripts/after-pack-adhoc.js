const { execFileSync } = require('node:child_process')
const path = require('node:path')

/**
 * electron-builder skips signing when mac.identity is null, but Apple Silicon
 * requires at least an ad-hoc signature for every Mach-O binary. Sign the
 * assembled app (including the bundled Python backend and Electron helpers)
 * before the DMG stage so the downloaded app is runnable after the documented
 * quarantine removal.
 */
exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') return

  const appName = context.packager.appInfo.productFilename
  const appPath = path.join(context.appOutDir, `${appName}.app`)

  // Extended attributes from the build tree can make codesign reject the bundle
  // ("resource fork, Finder information, or similar detritus not allowed").
  execFileSync('/usr/bin/xattr', ['-cr', appPath], { stdio: 'inherit' })
  execFileSync('/usr/bin/codesign', ['--force', '--deep', '--sign', '-', appPath], {
    stdio: 'inherit',
  })
  execFileSync('/usr/bin/codesign', ['--verify', '--deep', '--strict', appPath], {
    stdio: 'inherit',
  })
  console.log(`[afterPack] ad-hoc signed ${appPath}`)
}
