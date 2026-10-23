APPLY_BUTTON = "*[data-view-name='job-apply-button']"
JOB_DESC = "div[componentkey*='JobDetails_AboutTheJob']"
JOB_TITLE = "a[href*='/jobs/view/']"
JOB_COMPANY = "a[href*='/company/']"

NEXT = "#interop-outlet::shadow button[aria-label='Continue to next step']"
REVIEW = "#interop-outlet::shadow button[aria-label='Review your application']"
SUBMIT = "#interop-outlet::shadow button[aria-label='Submit application']"
CLOSE = "#interop-outlet::shadow button[aria-label='Dismiss']"
DISCARD_BUTTON = "#interop-outlet::shadow button[data-control-name*='discard']"
FIELD_ERROR = "#interop-outlet::shadow .artdeco-inline-feedback__message"

SHADOW_ROOT = "#interop-outlet::shadow "
UPLOAD_RESUME_INPUT = "#interop-outlet::shadow input[id*='jobs-document-upload-file-input-upload-resume']"
UPLOAD_COVER_LETTER_INPUT = "#interop-outlet::shadow textarea[aria-label='Cover letter']"
FOLLOW = "#interop-outlet::shadow input[id*='follow-company-checkbox']"
SEARCH_RESULTS = ".jobs-search-results__list"

# Selectors used within shadow DOM of interop-outlet
FIELDS = "div[class*='fb-dash-form-element']"
RADIO = "input[type='radio']"
CHECKBOX = "input[type='checkbox']"
TEXT = ".artdeco-text-input--input"
ERROR = ".artdeco-inline-feedback__message"

LIMIT_REACHED_DIALOG = "dialog[data-testid='dialog']"