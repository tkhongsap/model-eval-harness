input_template = """
Report summary of call transcripts from Telecom customer service center.
Date: ${date}

Table name: Number of customer reasons from call transcripts per day
------------------------------------------------------------------------------------
${reason}


Table name: Number of call center results from call transcripts per day
------------------------------------------------------------------------------------
${call_result}


Table name: Number of detected events from call transcripts per day
------------------------------------------------------------------------------------
${call_event_detection}


Table name: Main reasons and keywords for churned or cancelled customers per day
------------------------------------------------------------------------------------
${keyword_main}
"""