# Pilot FD Training Sign-in v1

`template.json` is the versioned field map for the currently known form revision. All boxes use normalized `[x, y, width, height]` coordinates relative to the 2614 x 3554 Checkpoint 2 cleaned master.

The field map includes handwritten text fields, selectable labels/mark areas, 19 attendee rows split into unit ID, print-name, and excluded signature cells, apparatus options, and the description area. Signature regions remain mapped for table geometry and diagnostics but are explicitly excluded from cropping, recognition, scoring, validation, and export. It contains no source image or extracted personal data.

Alignment rotates the incoming scan to the best of four cardinal orientations, estimates small skew, crops scanner margins, and scales into master coordinates. Processing must stop when form coverage is below 0.70, minimum anchor coverage is below 0.67, or absolute deskew exceeds 4 degrees. These deliberately conservative initial thresholds must be reevaluated with more approved samples; they are not recognition confidence scores.
