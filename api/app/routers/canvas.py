"""Canvas persistence.

The frontend owns what a widget *looks* like; this router only remembers that a
widget of some type sits at some position with some config blob. That split is
what lets a new widget be added tomorrow without touching the backend at all.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..models import CanvasNode

router = APIRouter(prefix="/api/canvas", tags=["canvas"])


@router.get("/nodes", response_model=list[CanvasNode])
def list_nodes(session: Session = Depends(get_session)):
    return session.exec(select(CanvasNode)).all()


@router.post("/nodes", response_model=CanvasNode, status_code=201)
def create_node(node: CanvasNode, session: Session = Depends(get_session)):
    node.id = None
    session.add(node)
    session.commit()
    session.refresh(node)
    return node


@router.patch("/nodes/{node_id}", response_model=CanvasNode)
def update_node(
    node_id: int, patch: dict, session: Session = Depends(get_session)
):
    node = session.get(CanvasNode, node_id)
    if node is None:
        raise HTTPException(404, "node not found")
    for key, value in patch.items():
        if key in {"x", "y", "width", "height", "config", "widget_type"}:
            setattr(node, key, value)
    session.add(node)
    session.commit()
    session.refresh(node)
    return node


@router.delete("/nodes/{node_id}", status_code=204)
def delete_node(node_id: int, session: Session = Depends(get_session)):
    node = session.get(CanvasNode, node_id)
    if node is None:
        raise HTTPException(404, "node not found")
    session.delete(node)
    session.commit()
