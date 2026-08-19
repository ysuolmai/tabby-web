import 'zone.js'
import 'core-js/proposals/reflect-metadata'
import 'core-js/features/array/flat'
import 'rxjs'
import { publishFacade } from '@angular/compiler'

import { enableProdMode } from '@angular/core'
import { platformBrowserDynamic } from '@angular/platform-browser-dynamic'

import './styles.scss'
import { AppModule } from './app.module'

// @angular/compiler is marked as side-effect-free, but Angular libraries may
// still need its JIT facade at runtime when the linker leaves partial metadata.
publishFacade(window)

if (!location.hostname.endsWith('.local')) {
  enableProdMode()
}

document.addEventListener('DOMContentLoaded', () => {
  platformBrowserDynamic().bootstrapModule(AppModule)
})
