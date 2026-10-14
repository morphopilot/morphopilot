import React from "react";
import { createRoot } from "react-dom/client";
import StudioWorkspace from "../app/studio";
import DataLibraryPage from "../components/data-library-page";
import {resolveDataCategory} from "../lib/data-categories";
import "../app/globals.css";

const params = new URLSearchParams(window.location.search);
const category = resolveDataCategory(params.get("category"),params.get("dataset"));
createRoot(document.getElementById("root")!).render(
  window.location.pathname.replace(/\/$/, "") === "/library"
    ? <DataLibraryPage initialWorkspace={category}/>
    : <StudioWorkspace initialWorkspace={category} initialSampleId={params.get("sample") || undefined} initialCollection={params.get("collection")==="experiments"?"experiments":undefined} initialVolumeId={params.get("volume") || undefined}
        initialTileId={params.get("tile") || undefined} initialZ={params.get("z") || undefined} />
);
