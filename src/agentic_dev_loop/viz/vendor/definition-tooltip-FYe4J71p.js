/**
* @license
*
* Copyright IBM Corp. 2019, 2026
*
* This source code is licensed under the Apache-2.0 license found in the
* LICENSE file in the root directory of this source tree.
*
* Also refer to the following links for third-party dependencies:
* https://www.npmjs.com/package/@floating-ui/core
* https://www.npmjs.com/package/@floating-ui/dom
* https://www.npmjs.com/package/@floating-ui/utils
* https://www.npmjs.com/package/@ibm/telemetry-js
* https://www.npmjs.com/package/@lit-labs/signals
* https://www.npmjs.com/package/@lit/context
* https://www.npmjs.com/package/@lit/reactive-element
* https://www.npmjs.com/package/lit
* https://www.npmjs.com/package/lit-element
* https://www.npmjs.com/package/lit-html
* https://www.npmjs.com/package/lodash-es
* https://www.npmjs.com/package/tslib
*/
import{c as e,d as t,n,t as r}from"./decorate-1Ivfrp93.js";import{n as i,t as a}from"./decorators-CNcdZSLL.js";import"./settings-DlHDxgf_.js";import{t as o}from"./carbon-element-Bi7pQHAl.js";import{n as s}from"./floating-controller-DmTRVV0A.js";import{n as c}from"./deep-shadow-contains-Cs2rSnhr.js";import{t as l}from"./tooltip-DYtXwn-o.js";import"./popover.min.js";let u=class extends n{constructor(...e){super(...e),this.align=`bottom`,this.autoalign=!1,this.defaultOpen=!1,this.openOnHover=!1,this.open=!1,this._tooltipId=`cds--definition-tooltip-${Math.random().toString(16).slice(2)}`}connectedCallback(){super.connectedCallback(),t(this.renderRoot,[c,l]),this.hasAttribute(`default-open`)&&(this.open=!0)}_handleBlur(){this.open=!1}_handleMouseDown(){this.open=!this.open}_handleKeyDown(e){let{key:t}=e;t===`Esc`||t===`Escape`?this.open&&=(e.stopPropagation(),!1):(t===` `||t===`Enter`)&&(e.preventDefault(),this.open=!this.open)}_handleMouseEnter(){this.openOnHover&&(this.open=!0)}_handleMouseLeave(){this.open=!1}_handleFocus(){this.open=!0}render(){let{align:t,open:n,_tooltipId:r}=this;return e`
      <cds-popover
        @mouseenter=${this._handleMouseEnter}
        @mouseleave=${this._handleMouseLeave}
        highContrast
        ?autoalign=${this.autoalign}
        .dropShadow=${!1}
        align=${t}
        .open=${n}>
        <button
          @focus=${this._handleFocus}
          @blur=${this._handleBlur}
          @mousedown=${this._handleMouseDown}
          @keydown=${this._handleKeyDown}
          aria-controls=${r}
          aria-describedby=${r}
          aria-expanded=${n}
          part="definition-term"
          class="${`cds`}--definition-term">
          <slot></slot>
        </button>
        <cds-popover-content id=${r}>
          <slot name="definition"></slot>
        </cds-popover-content>
      </cds-popover>
    `}static{this.styles=l}};r([i({reflect:!0,type:s})],u.prototype,`align`,void 0),r([i({type:Boolean,reflect:!0})],u.prototype,`autoalign`,void 0),r([i({type:Boolean,reflect:!0,attribute:`default-open`})],u.prototype,`defaultOpen`,void 0),r([i({reflect:!0,type:Boolean,attribute:`open-on-hover`})],u.prototype,`openOnHover`,void 0),r([a()],u.prototype,`open`,void 0),u=r([o(`cds-definition-tooltip`)],u);