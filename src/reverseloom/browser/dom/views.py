import hashlib
import re
from enum import IntEnum
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

def is_unstable_token(token: str) -> bool:
    if not token or len(token) > 80:
        return True
    if re.match(r"^(sc-|css-|emotion-|styled-|jss\d|svelte-s|_?css-|data-v-)", token):
        return True
    if re.search(r"__[a-zA-Z0-9]{4,}$", token):
        suffix = token.split("__")[-1]
        if re.search(r"\d", suffix) and re.search(r"[a-zA-Z]", suffix):
            return True
    if re.match(r"^[a-fA-F0-9]{5,}$", token) or re.match(r"^[0-9a-fA-F]{8}-", token):
        return True
    if 4 <= len(token) <= 10 and not re.search(r"[-_]", token):
        if re.search(r"(?:[a-zA-Z]+\d+[a-zA-Z]+)|(?:\d+[a-zA-Z]+\d+)", token):
            return True
    if re.search(r"[:\[\]<>{}\\/]", token) or re.match(r"^[\d]", token):
        return True
    return False

def get_stable_classes(class_str: str) -> List[str]:
    if not class_str:
        return []
    classes = class_str.split()
    return [c for c in classes if c and c.strip() and not is_unstable_token(c)]

class DOMRect(BaseModel):
    x: float
    y: float
    width: float
    height: float

class NodeType(IntEnum):
    ELEMENT_NODE = 1
    ATTRIBUTE_NODE = 2
    TEXT_NODE = 3
    CDATA_SECTION_NODE = 4
    PROCESSING_INSTRUCTION_NODE = 7
    COMMENT_NODE = 8
    DOCUMENT_NODE = 9
    DOCUMENT_TYPE_NODE = 10
    DOCUMENT_FRAGMENT_NODE = 11

class EnhancedAXProperty(BaseModel):
    name: str
    value: Any

class EnhancedAXNode(BaseModel):
    ax_node_id: str
    ignored: bool
    role: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    properties: List[EnhancedAXProperty] = Field(default_factory=list)
    child_ids: Optional[List[str]] = None

class EnhancedSnapshotNode(BaseModel):
    is_clickable: bool = False
    bounds: Optional[DOMRect] = None
    computed_styles: Dict[str, str] = Field(default_factory=dict)
    paint_order: Optional[int] = None
    cursor_style: Optional[str] = None

class EnhancedDOMTreeNode(BaseModel):
    node_id: int
    backend_node_id: int
    node_type: int
    node_name: str
    node_value: str
    attributes: Dict[str, str] = Field(default_factory=dict)
    is_visible: bool = False
    absolute_position: Optional[DOMRect] = None
    parent_node: Optional['EnhancedDOMTreeNode'] = None
    children_nodes: List['EnhancedDOMTreeNode'] = Field(default_factory=list)
    content_document: Optional['EnhancedDOMTreeNode'] = None
    shadow_roots: List['EnhancedDOMTreeNode'] = Field(default_factory=list)
    ax_node: Optional[EnhancedAXNode] = None
    snapshot_node: Optional[EnhancedSnapshotNode] = None
    frame_id: Optional[str] = None
    has_js_click_listener: bool = False
    compound_children: List[Dict[str, Any]] = Field(default_factory=list)
    is_scrollable: bool = False
    is_actually_scrollable: bool = False
    should_show_scroll_info: bool = False
    scroll_info: Optional[Dict[str, Any]] = None
    # Optional fields the serializer may read; default-safe so it never
    # AttributeErrors on nodes that predate these (shadow DOM / iframe hints).
    shadow_root_type: Optional[str] = None
    hidden_elements_info: Optional[List[Dict[str, Any]]] = None
    has_hidden_content: bool = False

    @property
    def tag_name(self) -> str:
        return self.node_name.lower()

    @property
    def children_and_shadow_roots(self) -> List['EnhancedDOMTreeNode']:
        return self.children_nodes + self.shadow_roots

    def get_scroll_info_text(self) -> str:
        if not self.scroll_info:
            return ""
        parts = []
        if self.scroll_info.get('pages_above', 0) > 0 or self.scroll_info.get('pages_below', 0) > 0:
            parts.append(f"{self.scroll_info.get('pages_above', 0):.1f} pages above, {self.scroll_info.get('pages_below', 0):.1f} pages below")
        if self.scroll_info.get('horizontal_scroll_percentage', 0) > 0:
            parts.append(f"horizontal {self.scroll_info.get('horizontal_scroll_percentage', 0):.0f}%")
        return " ".join(parts)

    def get_meaningful_text_for_llm(self) -> str:
        text = ""
        if self.ax_node and self.ax_node.name:
            text = self.ax_node.name
        elif self.node_value:
            text = self.node_value
        else:
            # Extract text from children
            texts = []
            def get_text(n):
                if n.node_name.lower() in {'script', 'style', 'noscript'}:
                    return
                if n.node_type == 3 and n.node_value:
                    texts.append(n.node_value)
                elif n.node_type == 1:
                    if n.node_name.lower() == 'img' and n.attributes and n.attributes.get('alt'):
                        texts.append(n.attributes.get('alt'))
                    elif n.attributes and n.attributes.get('aria-label'):
                        texts.append(n.attributes.get('aria-label'))
                for child in n.children_nodes:
                    get_text(child)
            get_text(self)
            text = " ".join(texts).strip()
            import re
            text = re.sub(r'\s+', ' ', text)
        return text

    @property
    def xpath(self) -> str:
        segments = []
        current = self
        while current and current.node_type in (1, 11): # ELEMENT_NODE or DOCUMENT_FRAGMENT_NODE
            if current.node_type == 11: # Skip shadow root, go to host
                current = current.parent_node
                continue

            if current.parent_node and current.parent_node.node_name == "IFRAME":
                break

            # calculate position
            position = 0
            if current.parent_node:
                siblings = [c for c in current.parent_node.children_nodes if c.node_type == 1 and c.node_name == current.node_name]
                if len(siblings) > 1:
                    try:
                        position = siblings.index(current) + 1
                    except ValueError:
                        pass

            tag_name = current.node_name.lower()
            xpath_index = f"[{position}]" if position > 0 else ""
            segments.insert(0, f"{tag_name}{xpath_index}")
            current = current.parent_node

        return "//" + "/".join(segments)

    @property
    def identity_key(self) -> str:
        """
        语义身份标识：tag + 结构属性 + 文字内容。
        文字作为附加维度，确保共享 CSS classes 的兄弟元素也能区分。
        """
        tag = self.node_name.lower()

        # 获取文字用于附加区分
        text = self.get_meaningful_text_for_llm()
        text_suffix = f"|t:{text[:30]}" if text else ""

        # 1. ID — globally unique when stable
        element_id = self.attributes.get('id', '')
        if element_id and not is_unstable_token(element_id):
            return f"{tag}|#{element_id}"

        # 2. Semantic attributes
        for attr in ['data-testid', 'data-test-id', 'data-qa', 'name', 'aria-label']:
            val = self.attributes.get(attr, '')
            if val and not is_unstable_token(val):
                return f"{tag}|{attr}={val}{text_suffix}"

        # 3. Classes + text
        classes = get_stable_classes(self.attributes.get('class', ''))
        if classes:
            return f"{tag}|.{'.'.join(classes[:3])}{text_suffix}"

        return f"{tag}|{self.backend_node_id}"

    @property
    def stable_hash(self) -> str:
        """
        确定性哈希：基于 identity_key + xpath。
        用作 ElementMappingService 的内部查找键，
        确保同一 session 内多次 rescan 时相同元素得到相同的 o_N 编号。
        """
        unique_seed = f"{self.identity_key}|{self.xpath}"
        return hashlib.md5(unique_seed.encode()).hexdigest()[:12]

class PropagatingBounds(BaseModel):
    tag: str
    bounds: DOMRect
    node_id: int
    depth: int

class SimplifiedNode(BaseModel):
    original_node: EnhancedDOMTreeNode
    children: List['SimplifiedNode']
    should_display: bool = True
    is_interactive: bool = False
    is_new: bool = False
    ignored_by_paint_order: bool = False
    excluded_by_parent: bool = False
    is_shadow_host: bool = False
    is_compound_component: bool = False

SimplifiedNode.model_rebuild()

class SerializedDOMState(BaseModel):
    _root: Optional[SimplifiedNode] = None
    xpath_map: Dict[int, EnhancedDOMTreeNode] = Field(default_factory=dict)

DOMXPathMap = Dict[int, EnhancedDOMTreeNode]
