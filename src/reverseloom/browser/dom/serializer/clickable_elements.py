from reverseloom.browser.dom.views import EnhancedDOMTreeNode, NodeType


class ClickableElementDetector:
	@staticmethod
	def is_interactive(node: EnhancedDOMTreeNode) -> bool:
		"""Check if this node is clickable/interactive using enhanced scoring."""

		def has_form_control_descendant(element: EnhancedDOMTreeNode, max_depth: int = 2) -> bool:
			"""Detect nested form controls within limited depth (handles label/span wrappers)."""
			if max_depth <= 0:
				return False

			for child in element.children_and_shadow_roots:
				if child.node_type != NodeType.ELEMENT_NODE:
					continue

				tag_name = child.tag_name
				if tag_name in {'input', 'select', 'textarea'}:
					return True

				if has_form_control_descendant(child, max_depth=max_depth - 1):
					return True

			return False

		# Skip non-element nodes
		if node.node_type != NodeType.ELEMENT_NODE:
			return False

		# remove html and body nodes
		if node.tag_name in {'html', 'body'}:
			return False

		# Check for JavaScript click event listeners detected via CDP (without DOM mutation)
		# this handles vue.js @click, react onClick, angular (click), etc.
		if node.has_js_click_listener:
			return True

		# IFRAME elements should be interactive if they're large enough to potentially need scrolling
		if node.tag_name and node.tag_name.upper() == 'IFRAME' or node.tag_name.upper() == 'FRAME':
			if node.snapshot_node and node.snapshot_node.bounds:
				width = node.snapshot_node.bounds.width
				height = node.snapshot_node.bounds.height
				# Only include iframes larger than 100x100px
				if width > 100 and height > 100:
					return True

		# Specialized handling for labels used as component wrappers (e.g., Ant Design radio/checkbox)
		if node.tag_name == 'label':
			# Skip labels that proxy via "for" to avoid double-activating external inputs
			if node.attributes and node.attributes.get('for'):
				return False

			# Detect labels that wrap form controls up to two levels deep (label > span > input)
			if has_form_control_descendant(node, max_depth=2):
				return True
			# Fall through to pointer/role/attribute heuristics for other label cases

		# Span wrappers for UI components (detect clear interactive signals only)
		if node.tag_name == 'span':
			if has_form_control_descendant(node, max_depth=2):
				return True
			# Allow other heuristics (aria roles, event handlers, pointer) to decide

		# SEARCH ELEMENT DETECTION: Check for search-related classes and attributes
		if node.attributes:
			search_indicators = {
				'search',
				'magnify',
				'glass',
				'lookup',
				'find',
				'query',
				'search-icon',
				'search-btn',
				'search-button',
				'searchbox',
			}

			# Check class names for search indicators
			class_list = node.attributes.get('class', '').lower().split()
			if any(indicator in ' '.join(class_list) for indicator in search_indicators):
				return True

			# Check id for search indicators
			element_id = node.attributes.get('id', '').lower()
			if any(indicator in element_id for indicator in search_indicators):
				return True

			# Check data attributes for search functionality
			for attr_name, attr_value in node.attributes.items():
				if attr_name.startswith('data-') and any(indicator in attr_value.lower() for indicator in search_indicators):
					return True

		# Enhanced accessibility property checks - direct clear indicators only
		if node.ax_node and node.ax_node.properties:
			for prop in node.ax_node.properties:
				try:
					# aria disabled
					if prop.name == 'disabled' and prop.value:
						return False

					# aria hidden
					if prop.name == 'hidden' and prop.value:
						return False

					# Direct interactiveness indicators
					if prop.name in ['focusable', 'editable', 'settable'] and prop.value:
						return True

					# Interactive state properties (presence indicates interactive widget)
					if prop.name in ['checked', 'expanded', 'pressed', 'selected']:
						# These properties only exist on interactive elements
						return True

					# Form-related interactiveness
					if prop.name in ['required', 'autocomplete'] and prop.value:
						return True

					# Elements with keyboard shortcuts are interactive
					if prop.name == 'keyshortcuts' and prop.value:
						return True
				except (AttributeError, ValueError):
					# Skip properties we can't process
					continue

				# ENHANCED TAG CHECK: Include truly interactive elements
		interactive_tags = {
			'button',
			'input',
			'select',
			'textarea',
			'a',
			'details',
			'summary',
			'option',
			'optgroup',
		}
		# Check with case-insensitive comparison
		if node.tag_name and node.tag_name.lower() in interactive_tags:
			return True

		# Tertiary check: elements with interactive attributes
		if node.attributes:
			# Check for event handlers or interactive attributes
			interactive_attributes = {'onclick', 'onmousedown', 'onmouseup', 'onkeydown', 'onkeyup', 'tabindex'}
			if any(attr in node.attributes for attr in interactive_attributes):
				return True

			# Check for interactive ARIA roles
			if 'role' in node.attributes:
				interactive_roles = {
					'button',
					'link',
					'menuitem',
					'option',
					'radio',
					'checkbox',
					'tab',
					'textbox',
					'combobox',
					'slider',
					'spinbutton',
					'search',
					'searchbox',
					'row',
					'cell',
					'gridcell',
				}
				if node.attributes['role'] in interactive_roles:
					return True

		# Quaternary check: accessibility tree roles
		if node.ax_node and node.ax_node.role:
			interactive_ax_roles = {
				'button',
				'link',
				'menuitem',
				'option',
				'radio',
				'checkbox',
				'tab',
				'textbox',
				'combobox',
				'slider',
				'spinbutton',
				'listbox',
				'search',
				'searchbox',
				'row',
				'cell',
				'gridcell',
			}
			if node.ax_node.role in interactive_ax_roles:
				return True

		# ICON AND SMALL ELEMENT CHECK: Elements that might be icons
		if (
			node.snapshot_node
			and node.snapshot_node.bounds
			and 10 <= node.snapshot_node.bounds.width <= 50  # Icon-sized elements
			and 10 <= node.snapshot_node.bounds.height <= 50
		):
			# Check if this small element has interactive properties
			if node.attributes:
				# Small elements with these attributes are likely interactive icons
				icon_attributes = {'class', 'role', 'onclick', 'data-action', 'aria-label'}
				if any(attr in node.attributes for attr in icon_attributes):
					return True

		# Final fallback: cursor style indicates interactivity (for cases Chrome missed)
		if node.snapshot_node and node.snapshot_node.cursor_style and node.snapshot_node.cursor_style == 'pointer':
			return True

		return False
