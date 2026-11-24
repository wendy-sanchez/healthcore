# Carpeta `uis`

Esta carpeta contiene **todas las interfaces de usuario** relacionadas con la compañía para el proyecto transversal de AI Engineering (por ejemplo: aplicaciones web, dashboards internos, portales de clientes, apps de Streamlit/Gradio, etc.).

Cada subcarpeta dentro de `uis/` debe corresponder a **una interfaz de usuario concreta** (por ejemplo `website`, `backoffice`) e incluir su propia documentación técnica y funcional.

- **Propósito principal**: centralizar en un único lugar todas las aplicaciones frontend que dan soporte a los casos de uso de la compañía.
- **Recomendación**: documenta en este archivo (o en sub-READMEs) las aplicaciones que vayas añadiendo, su objetivo, tecnología usada y cómo ejecutarlas.

## Aplicaciones actuales

- `website`: Sitio web corporativo público en Next.js + TypeScript. Landing page bilingüe (EN/ES) con marcado Schema.org, formulario de consulta, página Q&A RAG.
- `backoffice`: Panel de administración Next.js para operaciones internas. Directorio de proveedores, inventario médico, incidencias, telemetría, reporting mensual, visor de tickets RFP.
- `talent-pipeline-tracker`: App Next.js para seguimiento de candidatos. Filtros, vista detalle, actualización de estado, notas.
