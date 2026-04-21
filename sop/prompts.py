############################################
#
# Full
#
############################################

prompt__start: str = lambda task_descrip, ui_name : f"""# Task
Your job is to write a standard operating procedure (SOP) for a workflow.

# Workflow

The workflow is: "{task_descrip if task_descrip else 'Some unspecified digital task'}"

# User Interface

The workflow will be executed within a software application. The application is called: "{ui_name}". Feel free to rely on your latent background knowledge of {ui_name} to assist in completing the SOP.
"""

prompt__end: str = lambda : f"""Here is a sample format for what your SOP should look like:
```
1. Open the application by clicking its icon in the Dock or using Spotlight search (Cmd+Space, then type the app name).
2. Click on the text field labeled "Search" at the top of the window. Type the desired search term.
3. From the results list, click on the item that matches the desired name. The item name will be displayed in bold text.
4. Click the "Submit" button located near the bottom-right area of the form.
```

Note, the above SOP is just an example. Use the same format, but the actions will be different for your workflow.

IMPORTANT: Describe each UI element by its visual label, name, or role (e.g. "the 'Save' button", "the search text field", "the 'File' menu"). Do NOT use pixel coordinates or screen positions. Instead, describe elements by their appearance and context so the SOP works on any screen size or resolution.

Be as detailed as possible. Each step should be a discrete action that reflects what you see in the corresponding screenshot. Don't skip steps.

Please write your SOP below:"""

prompt__td: str = lambda task_descrip, ui_name: f"""{prompt__start(task_descrip, ui_name)}

# Instructions

Write an SOP for completing this workflow in this application. The SOP should simply contain an enumerated list of actions taken by the user to complete the given workflow.
In your SOP, list all of the actions taken (i.e., buttons clicked, fields entered, mouse scrolls etc.). Be descriptive about elements (i.e., 'the subheading located under the "General" section').

{prompt__end()}"""

prompt__td_kf: str = lambda task_descrip, ui_name: f"""{prompt__start(task_descrip, ui_name)}

# Instructions

You are given the following sequence of screenshots which were sourced from a demonstration of the workflow. 
The screenshots are presented in chronological order.

Given what you observe in the screenshots, write an SOP for completing the workflow in this application. The SOP should simply contain an enumerated list of actions taken by the user to complete the given workflow.
In your SOP, list all of the actions taken (i.e., buttons clicked, fields entered, mouse scrolls etc.). Describe each element by its label, name, role, or visual appearance -- NOT by coordinates or pixel positions. Be descriptive about elements (e.g., 'the "Save" button in the toolbar', 'the text field labeled "Search"').

{prompt__end()}"""

prompt__td_kf_act_intro: str = lambda task_descrip, ui_name: f"""{prompt__start(task_descrip, ui_name)}

# Workflow Demonstration

You are given the following sequence of screenshots which were sourced from a demonstration of the workflow. 
The screenshots are presented in chronological order.

Between each screenshot, you are also provided the action that was taken to transition between screenshots. 
However, the action is written in a simplified DSL (domain-specific language) that we use to describe actions taken by users. You will need to translate this into a natural language description of the action and add more details about what was happening, why, and what elements were interacted with.

Here are the screenshots and actions of the workflow:"""

prompt__td_kf_act_close: str = lambda : f"""
# Instructions

Given what you observe in the previous sequence of screenshots and DSL actions, write an SOP for completing the workflow for this specific interface. The SOP should simply contain an enumerated list of actions taken by the user to complete the given workflow.
In your SOP, list all of the actions taken (i.e., buttons clicked, fields entered, mouse scrolls etc.). Describe each element by its label, name, role, or visual appearance -- NOT by coordinates or pixel positions. Be descriptive about elements (e.g., 'the "Save" button in the toolbar', 'the text field labeled "Search"').

{prompt__end()}
"""

############################################
#
# Pairwise
#
############################################

prompt__start__pairwise: str = lambda task_descrip, ui_name : f"""# Task
Your job is to determine the single action that was taken between these screenshots were taken.

# User Interface

The software application where the screenshots are taken from is called: "{ui_name}". Feel free to rely on your latent background knowledge of {ui_name} to assist in completing the SOP.
"""

prompt__end__pairwise: str = lambda : f"""Here is a sample format for what your output should look like:
```
1. Click on the search text field at the top of the window to focus it. The placeholder text reads "Search...".
2. Type the desired search term into the text field.
```

Note, the above output is just an example. Use the same format, but the action might be different for your screenshots.
You might have only one item in your output, or you might have multiple items. It depends on the action that took place between the screenshots.
Be as detailed as possible. Each step should be a discrete action that reflects what you see in the screenshots. Don't skip steps. 
Only include the action that took place between the screenshots, and do not make any assumptions about what happened before or after the screenshots were taken.

Please write your output below:"""

prompt__td_kf__pairwise: str = lambda task_descrip, ui_name: f"""{prompt__start__pairwise(task_descrip, ui_name)}

# Instructions

You are given the following two screenshots.
The screenshots are presented in chronological order.
The first one was taken directly before the action was taken, and the second one was taken directly after the action was executed.
We are only interested in the specific action that was taken to transition between these two screenshots.

Given what you observe in the screenshots, write the step(s) corresponding to this action.
Make sure to list all of the details of the action that was taken to go from one screenshot to the other (i.e., buttons clicked, fields entered, mouse scrolls etc.). Describe each element by its label, name, role, or visual appearance -- NOT by coordinates or pixel positions. Be descriptive about elements (e.g., 'the "Save" button in the toolbar', 'the text field labeled "Search"').

{prompt__end__pairwise()}"""


prompt__td_kf_act_intro__pairwise: str = lambda task_descrip, ui_name: f"""{prompt__start__pairwise(task_descrip, ui_name)}

# Workflow Demonstration

You are given the following two screenshots which were sourced from a demonstration of the workflow. 
The screenshots are presented in chronological order.
The first one was taken directly before the action was taken, and the second one was taken directly after the action was executed.
Note that these screenshots could have been taken at any step of the workflow.

Between each screenshot, you are also provided the action that was taken to transition between screenshots. 
However, the action is written in a simplified DSL (domain-specific language) that we use to describe actions taken by users. You will need to translate this into a natural language description of the action and add more details about what was happening, why, and what elements were interacted with.

Here are the screenshots and action of this specific step from the larger workflow:"""

prompt__td_kf_act_close__pairwise: str = lambda : f"""
# Instructions

Given what you observe in the screenshots and DSL action, write the step(s) corresponding to this action that would go into a larger SOP for completing the workflow in this application. 
Make sure to list all of the actions taken to go from one screenshot to the other (i.e., buttons clicked, fields entered, mouse scrolls etc.). Describe each element by its label, name, role, or visual appearance -- NOT by coordinates or pixel positions. Be descriptive about elements (e.g., 'the "Save" button in the toolbar', 'the text field labeled "Search"').

{prompt__end__pairwise()}
"""

prompt__join_pairwise: str = lambda sop, separator : f"""
Your job is to create a standard operating procedure (SOP) for a workflow that outlines each step taken to complete the workflow.

Previously, you were given pairs of consecutive screenshots taken from a longer sequence of screenshots of a workers doing the workflow. You were asked to write the step(s) taken between each screenshot. Our goal is to compile these smaller sets of steps into a larger SOP for completing the entire workflow.

I've copied your responses for this previous pairwise screenshot analysis below. Each pair of screenshots is separated by {separator}. 

```
{sop}
```

Your job now is to combine these steps into a single, coherent SOP for completing the entire workflow. The steps are already ordered chronologically, so you do not need to worry about the ordering of the steps. 
Instead, you should remove any duplicate steps and ensure that the steps flow logically from one to the next.
Make sure each step is a discrete action. If a step is distinct but similar to another step, you must keep them as separate steps. Do not combine multiple actions into a single step.

Please write your unified SOP below:
"""



prompt__td_kf_act_intro__pairwise__cropped: str = lambda task_descrip, ui_name: f"""{prompt__start__pairwise(task_descrip, ui_name)}

# Workflow Demonstration

You are given the following three screenshots which were sourced from a demonstration of the workflow. The screenshots are presented in chronological order.

The first one was taken directly before the action was taken.
The second one is a zoomed-in version of the first screenshot, showing the area where the action was executed. You should use this zoomed-in screenshot to help identify the exact elements that were interacted with.
The third one was taken directly after the action was executed.

Note that these screenshots could have been taken at any step of the workflow.

Between the screenshots, you are also provided with the action that was taken to transition between screenshots. 
However, the action is written in a simplified DSL (domain-specific language) that we use to describe actions taken by users. You will need to translate this into a natural language description of the action and add more details about what was happening, why, and what elements were interacted with.

Here are the screenshots and action of this specific step from the larger workflow:"""

prompt__td_kf_act_close__pairwise__cropped: str = lambda : f"""
# Instructions

Given what you observe in the screenshots and DSL action, write the step(s) corresponding to this action that would go into a larger SOP for completing the workflow in this application. 
Make sure to list all of the actions taken to go from one screenshot to the other (i.e., buttons clicked, fields entered, mouse scrolls etc.). Describe each element by its label, name, role, or visual appearance -- NOT by coordinates or pixel positions. Be descriptive about elements (e.g., 'the "Save" button in the toolbar', 'the text field labeled "Search"').

{prompt__end__pairwise()}
"""


############################################
#
# Fix / Regenerate from failed step
#
############################################

prompt__fix_sop: str = lambda old_sop, failed_step, failed_step_text, failure_reason: f"""# Task
You previously generated this SOP (Standard Operating Procedure):

```
{old_sop}
```

An AI agent tried to execute this SOP but FAILED at step {failed_step}:
"{failed_step_text}"

Failure reason: {failure_reason}

# Instructions

Rewrite the SOP starting from step {failed_step}. Keep steps 1 through {failed_step - 1} exactly as they are — do not modify them at all.

Rewrite step {failed_step} and all subsequent steps to fix the issue. Make the instructions more specific and actionable. Avoid ambiguous descriptions of UI elements.

Tips for fixing:
- If the element was not found, describe it more precisely (use exact label text, surrounding context, or visual position relative to other elements).
- If the app was in an unexpected state, add steps to navigate to the correct state first.
- If a step was too vague, break it into smaller, concrete sub-steps.
- Use the screenshot below (if provided) to understand the actual screen state when the agent got stuck.

IMPORTANT: Describe each UI element by its visual label, name, or role (e.g. "the 'Save' button", "the search text field", "the 'File' menu"). Do NOT use pixel coordinates or screen positions.

Please write the complete rewritten SOP below (including the unchanged early steps):"""


############################################
#
# Generalize
#
############################################

prompt__generalize: str = lambda sop, task_descrip : f"""# Task
Your job is to create a standard operating procedure (SOP) for a workflow that outlines each step needed to complete the workflow.

The workflow is: `{task_descrip}`

Previously, you viewed one specific demonstration of this workflow. You then wrote down all the steps taken to complete that specific demonstration.

However, the workflow can be completed in several different ways. Your job now is to generalize the steps you wrote down for the specific demonstration into a more general SOP that can be used to complete the workflow in more robust contexts (i.e. different initial start states, window sizes, button locations, scrollable amounts, etc.).

Here are some examples of modifications you could make to an SOP to generalize it:
* Instead of "Scroll down 132px." => Look to the following step for the next element that is interacted with (e.g. a "Submit" button) and generalize this to "Scroll down until the Submit button is visible"
* Instead of "Press backspace four times to remove the characters typed into the text field." => Generalize this to "Clear out any existing text in the text field."
* Instead of "Click the button located at (1481px, 45px)." => Generalize this to "Click the button labeled 'Order' at the top-left corner of the screen next to the "Summary" tab."
Note that the above are just examples, so they may not apply to the SOP below.

Here is the SOP you generated previously for one specific demonstration of this workflow:

```
{sop}
```

Your job now is to replace any hyper-specific references in this SOP that would only apply to the specific demonstration you saw with more general references that could apply to any demonstration of this workflow. For example, anything with pixel coordinates or specific locations on the screen should be replaced with more general references.
Do not remove any steps from the SOP, just generalize them.
Do not add any new steps to the SOP, just generalize the existing ones.
Do not make any assumptions about the workflow that are not explicitly mentioned in the SOP.

Please write your generalized SOP below:
"""