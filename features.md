# Features
- Command-line interface (CLI) for easy interaction.
- Loading CV/resume files given file paths, and self-introduction text.
- Signs the user in LinkedIn through a Selenium browser instance.
- Searches for job postings based on user-defined criteria (e.g., job title, location, experience level).
- Extracts job descriptions from the postings, choose the most relevant ones based on the user's CV/resume.
- Automatically selects the CV/resume that best matches the job description, answer any required application questions based on the self-introduction text, and submits the application.

# Technical Specs
- Implemented in Python.
- Uses Selenium for web automation to interact with LinkedIn.
- Allow configuration for job search criteria, number of applications per day, login information, and other parameters via configuration files.
- Humanized behavior: randomized times between 9am to 12am, with delays between actions.
- Apply to jobs posted within a certain timeframe (e.g., last hour to last 7 days).
- Runs without user intervention after initial setup.
- Report the applied jobs in a CSV file, updated after each application.
- Use LLM to analyze job descriptions and answer application questions.
- Skip application if resume is not relevant to the job description.
- TODO: fill every input fields, not just the required ones.


# Non-Functional Requirements
- Ensure compliance with LinkedIn's terms of service to avoid account suspension.
- Implement error handling to manage issues like network failures or changes in LinkedIn's web structure, notifying the user as necessary (in a log).
- Maintain user privacy and data security, especially regarding login credentials and personal information.

## Use cases
- As a job seeker, I want to automate the process to search and apply for for the latest and most relevant job postings that matches my skills, such that I can save time and effort in finding job opportunities.