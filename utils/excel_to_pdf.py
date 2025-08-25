import boto3
import random
import openai
import logging
import datetime
import json
import pandas as pd
import os
import boto3
import pandas as pd
from io import StringIO
# =========================
# 🔧 Configuration
# =========================

# Constants
AWS_REGION = "us-west-1"
DEFAULT_LANGUAGE = "english"

# GPT models
GPT_MODELS = {
    'offense_success': 'ft:gpt-3.5-turbo-0125:personal:off-success-bot:93LovQuu',
    'defense_success': 'ft:gpt-3.5-turbo-0125:personal:def-success-bot:94QAWOFR',
    'summary_model': 'ft:gpt-3.5-turbo-0125:personal:summary-data-model:9OAYWgGP'
}

# API Keys
openai.api_key = os.getenv("OPENAI_API_KEY")

# AWS Translate Client
translate = boto3.client(service_name='translate', region_name=AWS_REGION, use_ssl=True)

# Logger setup
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Optional: Pretty log formatting
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


# Function to handle translation based on language with custom replacements
def translate_text(text, target_language):
    # Check if the target language is Portuguese and perform translation
    if target_language.lower() == 'portuguese':
        result = translate.translate_text(Text=text, SourceLanguageCode="en", TargetLanguageCode="pt")
        translated_text = result.get('TranslatedText')
        
        # Apply custom word replacements for specific terms in Portuguese
        replacements = {
            'atletas': 'atleta',
            'Submissões': 'Finalização',
            'partidas': 'Lutas',
            'Partida': 'Luta',
            'Win Ratio': 'Taxa/Porcentagem de vitória'
        }

        # Iterate over the replacements dictionary and apply them to the translated text
        for original, replacement in replacements.items():
            translated_text = translated_text.replace(original, replacement)
        
        return translated_text
    
    # If the target language is English, return the original text
    return text

def model_generate_response(model_name, prompt, allowed_moves_str, temperature=0.4, max_tokens=250):
    """
    Generates a response using the fine-tuned model.

    Parameters:
    - prompt (str): The input text to the model.
    - temperature (float): The creativity of the response. Lower values are more deterministic.
    - max_tokens (int): The maximum length of the model's response.

    Returns:
    - str: The generated response text.
    """
    
    try:
        
        gpt_role = {
            "role": "system", 
            "content": f"You are a Jiu Jitsu Strategy Expert capable of analyzing match data and providing strategic insights. Only use the following moves: {allowed_moves_str} in your analysis and do not introduce any new moves."
                }
        user_content = {"role": "user", "content": prompt}
        # Generate the model's response using the chat completions endpoint
        response = openai.ChatCompletion.create(
            model=model_name,
            messages=[
                gpt_role, user_content,
            ],
            temperature=temperature,
            max_tokens=max_tokens
        )

        # Extract the response from the chat completion
        response_text = response['choices'][0]['message']['content']

        return response_text.strip()
    except Exception as e:
        # Handle potential errors (e.g., API issues, invalid inputs)
        print(f"An error occurred: {e}")
        return "Error generating response."

def gender_neutral_model(prompt, model_name="gpt-4", temperature=0.4, max_tokens=250):
    """
    Generates a response using the specified model.

    Parameters:
    - prompt (str): The input text to the model.
    - model_name (str): The name of the model to use.
    - temperature (float): The creativity of the response. Lower values are more deterministic.
    - max_tokens (int): The maximum length of the model's response.

    Returns:
    - str: The generated response text.
    """
    try:
        # Define the system role for the model
        gpt_role = {"role": "system", "content": "You are a language expert capable of converting text to gender-neutral language. Provide only the converted text without any additional information."}
        user_content = {"role": "user", "content": f"Convert the following paragraph to gender-neutral language: {prompt}"}
        
        # Generate the model's response using the chat completions endpoint
        response = openai.ChatCompletion.create(
            model=model_name,
            messages=[gpt_role, user_content],
            temperature=temperature,
            max_tokens=max_tokens
        )

        # Extract the response from the chat completion
        response_text = response['choices'][0]['message']['content']

        return response_text.strip()
    except Exception as e:
        # Handle potential errors (e.g., API issues, invalid inputs)
        print(f"An error occurred: {e}")
        return "Error generating response."


def model_check_for_analysis(json_data, model_name='gpt-4o', temperature=0.2, max_tokens=500):
    """
    Generates a response using the fine-tuned model.

    Parameters:
    - prompt (str): The input text to the model.
    - temperature (float): The creativity of the response. Lower values are more deterministic.
    - max_tokens (int): The maximum length of the model's response.

    Returns:
    - str: The generated response text.
    """

    prompt = f"""
    Please analyze the provided data and focus solely on identifying weaknesses or shortcomings in the 
    athlete's performance. Do not highlight any positive aspects, 
    except when fewer defensive moves are attempted—this indicates that the athlete faced fewer attacks from the opposition,
    which is a positive scenario and we do not want to highlight that can be "potentially" a weakness. 
    Avoid providing suggestions for improvement on any move if the data is too limited
    (e.g., if there is only one or a very small number of attempts). 
    Your response should be "yes" or "no" based on whether there is 
    enough substantial data to make meaningful observations and suggestions for improvement.
    Here is the data for analysis:

    {json_data}
    """


    try:

        gpt_role = {"role": "system", "content": "You are a Jiu Jitsu Strategy Expert capable of analyzing match data and providing strategic insights."}
        user_content = {"role": "user", "content": prompt}
        # Generate the model's response using the chat completions endpoint
        response = openai.ChatCompletion.create(
            model=model_name,
            messages=[
                gpt_role, user_content,
            ],
            temperature=temperature,
            max_tokens=max_tokens
        )

        # Extract the response from the chat completion
        response_text = response['choices'][0]['message']['content']

        return response_text.strip()
    except Exception as e:
        # Handle potential errors (e.g., API issues, invalid inputs)
        print(f"An error occurred: {e}")
        return "Error generating response."


def calculate_submissions_summary(results_df, moves_df, stats_df):
    # Identify submission moves from the moves dataframe
    submission_moves = moves_df[moves_df['categorization'].str.contains('Submission', na=False)]['move_name'].tolist()
    
    # Initialize a dictionary to hold summary data
    submission_summary = {"wins": {}, "losses": {}, "offensive_threats": {}, "defensive_threats": {}}

    # Temporary storage for calculating ratios and percentages
    total_offensive_attempts = 0
    total_offensive_successes = 0
    total_defensive_attempts = 0
    total_defensive_successes = 0

    # Temporary storage for total offensive and defensive moves
    total_offensive_moves = 0
    total_defensive_moves = 0
    total_offensive_submission_moves = 0
    total_defensive_submission_moves = 0

    # Iterate over each match's stats to populate the summary
    for _, row in stats_df.iterrows():
        move_name = row['move_name']
        offense_attempted = row['offense_attempted']
        offense_succeeded = row['offense_succeeded']
        defense_attempted = row['defense_attempted']
        defense_succeeded = row['defense_succeeded']
        match = row['match']

        # Increment the total offensive and defensive moves
        total_offensive_moves += offense_attempted
        total_defensive_moves += defense_attempted

        # Count total offensive and defensive submission moves
        if move_name in submission_moves:
            total_offensive_submission_moves += offense_attempted
            total_defensive_submission_moves += defense_attempted

        # Check if the move is a submission move
        if move_name in submission_moves:
            match_result = results_df[results_df['match'] == match]['Result'].iloc[0]
            if offense_succeeded > 0 and match_result == 'Win':
                submission_summary["wins"][move_name] = submission_summary["wins"].get(move_name, 0) + offense_succeeded
            if defense_attempted > 0 and defense_attempted != defense_succeeded and match_result == "Lost":
                submission_summary["losses"][move_name] = submission_summary["losses"].get(move_name, 0) + 1 # + max(defense_attempted, offense_attempted)
            if offense_attempted > 0:
                submission_summary["offensive_threats"][move_name] = submission_summary["offensive_threats"].get(move_name, 0) + offense_attempted
                total_offensive_attempts += offense_attempted
                total_offensive_successes += offense_succeeded
            if defense_attempted > 0:
                submission_summary["defensive_threats"][move_name] = submission_summary["defensive_threats"].get(move_name, 0) + defense_attempted
                total_defensive_attempts += defense_attempted
                total_defensive_successes += defense_succeeded

    # Generate the summary lines
    summary_lines = []
    for category, details in submission_summary.items():
        if details:
            count = sum(details.values())
            # Special case for "loss" to handle singular and plural forms correctly
            if category == "losses":
                category_text = "Loss" if count == 1 else "Losses"
            else:
                category_text = category.replace("_", " ").title()[:-1] if count == 1 else category.replace("_", " ").title()
            moves_summary = ", ".join([f"{move} x{count}" for move, count in details.items()])
            summary_lines.append(f"{count} {category_text} – {moves_summary}")

            # Add Offensive and Defensive Submission Move % lines after "Offensive Threats" and "Defensive Threats" respectively
            if category == 'offensive_threats':
                if total_offensive_moves > 0:
                    offensive_submission_move_percentage = (total_offensive_submission_moves / total_offensive_moves) * 100
                    summary_lines.append(f"Offensive Submission Move Percentage – {offensive_submission_move_percentage:.2f}%")
                else:
                    summary_lines.append("Offensive Submission Move Percentage – N/A")
            elif category == 'defensive_threats':
                if total_defensive_moves > 0:
                    defensive_submission_move_percentage = (total_defensive_submission_moves / total_defensive_moves) * 100
                    summary_lines.append(f"Defensive Submission Move Percentage – {defensive_submission_move_percentage:.2f}%")
                else:
                    summary_lines.append("Defensive Submission Move Percentage – N/A")

    # Calculate and append success ratios
    if total_offensive_attempts > 0:
        offensive_ratio = (total_offensive_successes / total_offensive_attempts) * 100
        summary_lines.append(f"Offensive Submission Success Ratio – {offensive_ratio:.2f}%")
    else:
        summary_lines.append("Offensive Submission Success Ratio – N/A")

    if total_defensive_attempts > 0:
        defensive_ratio = (total_defensive_successes / total_defensive_attempts) * 100
        summary_lines.append(f"Defensive Submission Success Ratio – {defensive_ratio:.2f}%")
    else:
        summary_lines.append("Defensive Submission Success Ratio – N/A")

    return summary_lines



def calculate_match_type_statistics(results_data):
    """
    Calculates match type statistics from the given DataFrame.

    Parameters:
    - results_data: pandas DataFrame with columns ['Result', 'Match Type', 'match'].

    Returns:
    - A dictionary containing the counts for each match type where the count is greater than 0.
    """
    # Count the occurrences of each match type
    match_type_counts = results_data['Match Type'].value_counts()
    
    # Filter out match types with a count of 0 and convert to dictionary
    filtered_counts = match_type_counts[match_type_counts > 0].to_dict()
    
    return filtered_counts


def calculate_match_statistics(matches_data, moves_df, results_data):
    """
    Calculates match statistics from the given DataFrame.

    Parameters:
    - matches_data: pandas DataFrame with columns ['move_name', 'offense_attempted', 'offense_succeeded', 'defense_attempted', 'defense_succeeded', 'match'].
    - moves_df: pandas DataFrame with columns ['move_name', 'category', 'categorization', 'points'].
    - results_data: pandas DataFrame with columns ['Result', 'Match Type', 'match', 'Referee Decision', 'Disqualified?'].

    Returns:
    - A dictionary containing:
        - total_matches: Total number of matches.
        - wins: Number of wins.
        - losses: Number of losses.
        - draws: Number of draws (if any).
        - win_ratio: Win ratio as a percentage.
        - points_details: List of strings with points details for each match.
    """
    # Merge the match statistics data
    merged_data = matches_data.merge(moves_df, on='move_name', how='left')
    
    # Filter results_data for matches containing "Points"
    points_matches = results_data[results_data['Match Type'].str.contains("Points")]['match'].unique()
    
    # Group by match
    grouped = merged_data.groupby('match')
    
    points_details = []
    not_applicable_matches = []

    for match, group in grouped:
        if match in points_matches:
            # Calculate points for the player (successful offenses)
            player_points = (group['offense_succeeded'] * group['points']).sum()
            
            # Calculate points for the opponent (unsuccessful defenses)
            opponent_points = ((group['defense_attempted'] - group['defense_succeeded']) * group['points']).sum()

            # Append formatted points details
            points_details.append(f"{match} - {player_points} – {opponent_points} Points")
        else:
            not_applicable_matches.append(match.replace("-", " "))
    
    # Append the "Not Applicable" summary at the end
    if not_applicable_matches:
        points_details.append(f"{', '.join(map(str, not_applicable_matches))} - Not Applicable")
    
    # Calculate overall statistics
    total_matches = len(results_data['match'].unique())
    wins = (results_data['Result'] == 'Win').sum()
    losses = (results_data['Result'] == 'Lost').sum()
    draws = (results_data['Result'] == 'Draw').sum()
    win_ratio = round(wins / total_matches * 100, 2) if total_matches > 0 else 0
    
    # Calculate referee decisions
    wins_referee = ((results_data['Result'] == 'Win') & (results_data['Referee Decision'] == 'Yes')).sum()
    losses_referee = ((results_data['Result'] == 'Lost') & (results_data['Referee Decision'] == 'Yes')).sum()
    draws_referee = ((results_data['Result'] == 'Draw') & (results_data['Referee Decision'] == 'Yes')).sum()

    # Calculate disqualifications
    wins_dq = ((results_data['Result'] == 'Win') & (results_data['Disqualified?'] == 'Yes')).sum()
    losses_dq = ((results_data['Result'] == 'Lost') & (results_data['Disqualified?'] == 'Yes')).sum()
    draws_dq = ((results_data['Result'] == 'Draw') & (results_data['Disqualified?'] == 'Yes')).sum()

    result = {
        'total_matches': total_matches,
        'wins': wins,
        'losses': losses,
        'win_ratio': win_ratio,
        'points_details': points_details,
        'wins_referee': wins_referee,
        'losses_referee': losses_referee,
        'draws_referee': draws_referee,
        'wins_dq': wins_dq,
        'losses_dq': losses_dq,
        'draws_dq': draws_dq,
    }
    
    if draws > 0:
        result['draws'] = draws
    
    return result


def analyze_most_successful_categorization(grouped_df):

    def text_formatting(percentage_of_total_success, max_success_category, moves_list):
        if len(moves_list) > 1:
            moves_str = ', '.join(moves_list[:-1]) + ' and ' + moves_list[-1]
        else:
            moves_str = moves_list[0]

        # Check if 'Position' is in max_success_category (case-insensitive)
        if 'position' in max_success_category.lower():
            category_str = max_success_category
        else:
            category_str = f'{max_success_category} Category'

        # Creating multiple formats for the sentence with the adjusted category string
        formats = [
            f'{percentage_of_total_success:.2f}% of all offensively successful attacks came from {category_str} ({moves_str}).',
            f'Offensive attacks from {category_str} were most successful, making up {percentage_of_total_success:.2f}% of all the offensively succesful attacks, involving moves like {moves_str}.',
            f'{category_str} leads with a {percentage_of_total_success:.2f}% success rate in offensive attacks, including techniques such as {moves_str}.',
            f'With moves such as {moves_str}, {category_str} accounts for {percentage_of_total_success:.2f}% of the success in offensive maneuvers.'
        ]

        # Return one of the sentences randomly
        return random.choice(formats)
    
    # Sum 'offense_succeeded' for each 'categorization'
    categorization_success = grouped_df.groupby('categorization')['offense_succeeded'].sum().reset_index()

    if categorization_success['offense_succeeded'].max() == 0:
        return "No successful offensive attacks were recorded.", False

    # Identify the 'categorization' with the highest 'offense_succeeded'
    max_success_categorization = categorization_success.loc[categorization_success['offense_succeeded'].idxmax()]
    
    # Filter moves from this 'categorization' that were successful at least once
    successful_moves = grouped_df[(grouped_df['categorization'] == max_success_categorization['categorization']) & (grouped_df['offense_succeeded'] > 0)]
    
    # Calculate the total percentage of offensively successful attacks from this categorization
    total_offense_success = grouped_df['offense_succeeded'].sum()
    if total_offense_success > 0:
        percentage_of_total_success = (max_success_categorization['offense_succeeded'] / total_offense_success) * 100
    else:
        # If no successful offenses are found
        return "No successful offensive attacks were recorded.", False
    
    # Constructing the output line
    moves_list = successful_moves['move_name'].tolist()
    output_line = text_formatting(percentage_of_total_success, max_success_categorization['categorization'], moves_list)
#     moves_str = ", ".join(moves_list)
#     output_line = f"{percentage_of_total_success:.2f}% of all offensively successful attacks came from {max_success_categorization['categorization']} ({moves_str})."
    
    return output_line, True
        
def analyze_most_attempted_offense_and_submission(grouped_df):
            
            # Total number of offense attempts
            total_offense_attempts = grouped_df['offense_attempted'].sum()
            
            if total_offense_attempts == 0:
                return "No offensive attempts were recorded.", False
            
            # Find the offense that was attempted the most and its details
            max_attempted_offense = grouped_df.loc[grouped_df['offense_attempted'].idxmax()]
            max_attempted_offense_count = max_attempted_offense['offense_attempted']
            max_attempted_offense_percentage = (max_attempted_offense_count / total_offense_attempts) * 100
            
            # Find the most attempted submission move
            submissions_df = grouped_df[grouped_df['categorization'] == 'Submission']
            if not submissions_df.empty:
                max_attempted_submission = submissions_df.loc[submissions_df['offense_attempted'].idxmax()]
                submission_message = f"{max_attempted_submission['move_name']} being the most attempted submission x{max_attempted_submission['offense_attempted']}."
            else:
                submission_message = "No submission was attempted."
            
            # Constructing the output line
            output_line = f"{max_attempted_offense['move_name']} attempts from {max_attempted_offense['categorization']} was attempted the most out of any position with {max_attempted_offense_count} attempts ({max_attempted_offense_percentage:.2f}%) with {submission_message}"
            
            return output_line, True
    
    
    
def analyze_most_successful_defense_categorization(grouped_df):
    def text_formatting(percentage_of_total_success, max_success_category, moves_list):
        if len(moves_list) > 1:
            moves_str = ', '.join(moves_list[:-1]) + ' and ' + moves_list[-1]
        else:
            moves_str = moves_list[0]
        
        category_str = f'{max_success_category} Category' if 'position' not in max_success_category.lower() else max_success_category

        formats = [
            f'{percentage_of_total_success:.2f}% of all successfully defended attempts came against {category_str} ({moves_str}).',
            f'Defensive attempts against {category_str} were most successful, making up {percentage_of_total_success:.2f}% of all successful defenses, involving moves like {moves_str}.',
            f'{category_str} leads with a {percentage_of_total_success:.2f}% defense success rate, including successful defending against moves such as {moves_str}.',
            f'With succuessful defensive attempts against moves such as {moves_str}, {category_str} accounts for {percentage_of_total_success:.2f}% of the success in defensive maneuvers.'
        ]
        return random.choice(formats)
    
    categorization_success = grouped_df.groupby('categorization')['defense_succeeded'].sum().reset_index()
    max_success_categorization = categorization_success.loc[categorization_success['defense_succeeded'].idxmax()]
    successful_moves = grouped_df[(grouped_df['categorization'] == max_success_categorization['categorization']) & (grouped_df['defense_succeeded'] > 0)]
    total_defense_success = grouped_df['defense_succeeded'].sum()

    if total_defense_success == 0:
        return "No successful defensive attempts were recorded.", False
    # Check if there are any successful defenses to avoid division by zero
    if total_defense_success > 0:
        percentage_of_total_success = (max_success_categorization['defense_succeeded'] / total_defense_success) * 100

    moves_list = successful_moves['move_name'].tolist()
    output_line = text_formatting(percentage_of_total_success, max_success_categorization['categorization'], moves_list)
    
    return output_line, True



def analyze_most_successful_defense_categorization(grouped_df):
    def text_formatting(percentage_of_total_success, max_success_category, moves_list):
        if len(moves_list) > 1:
            moves_str = ', '.join(moves_list[:-1]) + ' and ' + moves_list[-1]
        else:
            moves_str = moves_list[0]
        
        category_str = f'{max_success_category} Category' if 'position' not in max_success_category.lower() else max_success_category

        formats = [
            f'{percentage_of_total_success:.2f}% of all successfully defended attempts came against {category_str} ({moves_str}).',
            f'Defensive attempts against {category_str} were most successful, making up {percentage_of_total_success:.2f}% of all successful defenses, involving moves like {moves_str}.',
            f'{category_str} leads with a {percentage_of_total_success:.2f}% defense success rate, including successful defending against moves such as {moves_str}.',
            f'With succuessful defensive attempts against moves such as {moves_str}, {category_str} accounts for {percentage_of_total_success:.2f}% of the success in defensive maneuvers.'
        ]
        return random.choice(formats)
    
    categorization_success = grouped_df.groupby('categorization')['defense_succeeded'].sum().reset_index()
    max_success_categorization = categorization_success.loc[categorization_success['defense_succeeded'].idxmax()]
    successful_moves = grouped_df[(grouped_df['categorization'] == max_success_categorization['categorization']) & (grouped_df['defense_succeeded'] > 0)]
    total_defense_success = grouped_df['defense_succeeded'].sum()

    if total_defense_success == 0:
        return "No successful defensive attempts were recorded.", False
    # Check if there are any successful defenses to avoid division by zero
    if total_defense_success > 0:
        percentage_of_total_success = (max_success_categorization['defense_succeeded'] / total_defense_success) * 100

    moves_list = successful_moves['move_name'].tolist()
    output_line = text_formatting(percentage_of_total_success, max_success_categorization['categorization'], moves_list)
    
    return output_line, True


def analyze_most_attempted_defense_and_submission(grouped_df):
    total_defense_attempts = grouped_df['defense_attempted'].sum()

    # Early check if there are any defense attempts at all
    if total_defense_attempts == 0:
        return "No defense attempts were recorded by the opposition.", False
    max_attempted_defense = grouped_df.loc[grouped_df['defense_attempted'].idxmax()]
    max_attempted_defense_count = max_attempted_defense['defense_attempted']
    # Check if there are any defense attempts to avoid division by zero
    if total_defense_attempts > 0:
        max_attempted_defense_percentage = (max_attempted_defense_count / total_defense_attempts) * 100
    else:
        max_attempted_defense_percentage = 0

    submissions_df = grouped_df[(grouped_df['categorization'] == 'Submission') & (grouped_df['defense_attempted'] > 0)]
    submission_message = "No submission was attempted by the opposition."
    if not submissions_df.empty:
        max_attempted_submission = submissions_df.loc[submissions_df['defense_attempted'].idxmax()]
        submission_message = f"{max_attempted_submission['move_name']} being the most attempted submission move by opposition x{max_attempted_submission['defense_attempted']}."

    output_line = f"The {max_attempted_defense['move_name']} from {max_attempted_defense['categorization']} was the most attempted move by the opposition, with {max_attempted_defense_count} attempts accounting for {max_attempted_defense_percentage:.2f}% of all moves, while {submission_message}"
    
    return output_line, True



# =========================
# 📊 Data Preparation
# =========================
def load_data(moves_df, xls, athlete_sheet):

    athlete_df = pd.read_excel(xls, sheet_name=athlete_sheet)
    athlete_name = athlete_df.at[0, 'Name']
    athlete_language = athlete_df.at[0, 'Language'].lower()

    # Collect Stats sheets
    stats_df = pd.concat([
        pd.read_excel(xls, sheet_name=s).assign(match=s.split(" ")[0])
        for s in xls.sheet_names if "Match-" in s and "Stats" in s
    ])
    
    # Collect Result sheets
    results_df = pd.concat([
        pd.read_excel(xls, sheet_name=s).assign(match=s.split(" ")[0])
        for s in xls.sheet_names if "Match-" in s and "Result" in s
    ])

    # Load and clean moves data
    moves_df.rename(columns={'Categorization': 'categorization', 'Points': 'points'}, inplace=True)

    return athlete_name, athlete_language, stats_df, results_df, moves_df



def prepare_grouped_data(stats_df, moves_df):
    merged = stats_df.merge(moves_df[['move_name', 'categorization']], on='move_name', how='left')
    return merged.groupby(['move_name', 'categorization']).sum(numeric_only=True).reset_index()


def get_top_non_zero(df, column, top_n=7):
    filtered = df[df[column] > 0]
    sorted_df = filtered.sort_values(by=column, ascending=False)
    return {
        "labels": sorted_df["move_name"].head(top_n).tolist(),
        "values": sorted_df[column].head(top_n).tolist()
    }


# =========================
# 🧠 AI Model Analysis
# =========================
def generate_summary(stats_df, results_df, name, language):
    matches_data = {
        'Stats': stats_df,
        'Results': results_df
    }

    def df_to_dict(df):
        return df.to_dict(orient='records')

    athlete_data = {
        'Name': str(name),
        'Matches Data': {
            'Stats': df_to_dict(matches_data['Stats']),
            'Results': df_to_dict(matches_data['Results'])
        }
    }

    json_data = json.dumps(athlete_data, indent=4)
    moves_list_str = ", ".join(matches_data['Stats']['move_name'].unique().tolist())

    message_to_pass = f"Please analyze this data: {json_data}"
    response = model_generate_response(GPT_MODELS['summary_model'], message_to_pass, moves_list_str, temperature=0.4, max_tokens=250)
    response = gender_neutral_model(response.strip("[]")).strip()

    translated = translate_text(response, language)
    logging.info(f"Translated Summary: {translated}")
    return translated, json_data


# =========================
# 🧾 Main PDF Data Builder
# =========================
def build_pdf_dict(name, language, stats_df, results_df, moves_df, grouped_df, summary_text, json_data):
    submission_summary = calculate_submissions_summary(results_df, moves_df, stats_df)
    match_stats = calculate_match_statistics(stats_df, moves_df, results_df)

    wins_line = f"{match_stats['wins']} Wins"
    if match_stats.get("wins_referee", 0) > 0:
        wins_line += f" ({match_stats['wins_referee']}x Referee Decision)"

    summary_lines = [
        f"{match_stats['total_matches']} matches",
        wins_line,
        f"{match_stats['losses']} Losses",
        f"{match_stats['win_ratio']}% Win Ratio"
    ]

    # disclaimer_summary = ""
    # if "no" in model_check_for_analysis(json_data).lower():
    #     disclaimer_text = "Disclaimer: The athlete's data is insufficient for reliable suggestions, so recommendations may not be accurate."
    #     disclaimer_summary = translate_text(disclaimer_text, language)

    graph_data = {
        "offense_successes": get_top_non_zero(grouped_df, "offense_succeeded", 7),
        "offense_attempts": get_top_non_zero(grouped_df, "offense_attempted", 7),
        "defense_successes": get_top_non_zero(grouped_df, "defense_succeeded", 7),
        "defense_attempts": get_top_non_zero(grouped_df, "defense_attempted", 7),
    }

    return {
        "athlete_name": name,
        "report_date": datetime.datetime.now().strftime("%B %d, %Y"),
        "submissions": submission_summary,
        "match_types": [f"{v} {k}" for k, v in calculate_match_type_statistics(results_df).items()],
        "win/loss_ratio": summary_lines,
        "points": match_stats.get("points_details"),
        "offensive_analysis": {
            "successful": analyze_most_successful_categorization(grouped_df)[0],
            "attempted": analyze_most_attempted_offense_and_submission(grouped_df)[0],
        },
        "defensive_analysis": {
            "successful": analyze_most_successful_defense_categorization(grouped_df)[0],
            "attempted": analyze_most_attempted_defense_and_submission(grouped_df)[0],
        },
        "final_summary": {
            "text": summary_text,
            # "disclaimer": disclaimer_summary or None
        },
        "graph_data": graph_data
    }

def check_missing_sheets(xls, context):
    match_sheets = {name.split(" ")[0]: [] for name in xls.sheet_names if "Match-" in name}

    # Populate the dictionary with sheet types for each match
    for name in xls.sheet_names:
        if "Match-" in name:
            match_number = name.split(" ")[0]
            sheet_type = name.split(" ")[1]
            match_sheets[match_number].append(sheet_type)

    # Check for missing sheet types
    for match, types in match_sheets.items():
        if "Stats" not in types:
            context["errors"].append(f"{match} does not have Stats Sheet")
            context["has_errors"] = True
        if "Result" not in types:
            context["errors"].append(f"{match} does not have Result Sheet")
            context["has_errors"] = True


def validate_move_names(stats_df, moves_df, context):
    valid_moves = moves_df['move_name'].tolist()

    # Convert all valid moves to lowercase for case-insensitive comparison
    valid_moves_lower = [move.lower() for move in valid_moves]

    for index, row in stats_df.iterrows():
        move_name = row['move_name'].lower()  # Convert to lowercase for comparison
        if move_name not in valid_moves_lower:
            # If the move is not valid, add an error message
            context["errors"].append(f"The move {row['move_name']} is not a valid move, in {row['match']}")
            context["has_errors"] = True


def validate_and_clean_numeric_fields(stats_df, context):
    numeric_fields = ['offense_attempted', 'offense_succeeded', 'defense_attempted', 'defense_succeeded']
    rows_to_drop = []

    for index, row in stats_df.iterrows():
        for field in numeric_fields:
            value = row[field]
            if not isinstance(value, int):  # Check if value is not an integer
                try:
                    # Attempt to convert the field value to an integer
                    converted_value = int(float(value))
                    # Check if conversion from float to int changes the value (i.e., had a decimal part)
                    if float(value) != converted_value:
                        raise ValueError("Value cannot have a decimal part")
                except ValueError:
                    # Log error if conversion fails or value has a decimal part
                    context["errors"].append(f"Invalid integer value for {field} in move {row['move_name']} in {row['match']}. Value entered: {row[field]}")
                    context["has_errors"] = True
                    # Mark row for removal
                    if index not in rows_to_drop:
                        rows_to_drop.append(index)
    
    # Drop the marked rows from the DataFrame
    stats_df.drop(rows_to_drop, inplace=True, errors='ignore')
    # Reset index after dropping rows
    stats_df.reset_index(drop=True, inplace=True)


def validate_offense_attempts_vs_succeeds(stats_df, context):

    for index, row in stats_df.iterrows():
        if row['offense_attempted'] < row['offense_succeeded']:
            context["errors"].append(f"Offense attempts less than offense succeeded for move {row['move_name']} in {row['match']}. Attempts: {row['offense_attempted']}, Succeeded: {row['offense_succeeded']}")
            context["has_errors"] = True

def validate_defense_attempts_vs_succeeds(stats_df, context):

    for index, row in stats_df.iterrows():
        if row['defense_attempted'] < row['defense_succeeded']:
            context["errors"].append(f"Defense attempts less than defense succeeded for move {row['move_name']} in {row['match']}. Attempts: {row['defense_attempted']}, Succeeded: {row['defense_succeeded']}")
            context["has_errors"] = True

def validate_submission_rules(stats_df, moves_df, context):
    
    # Filter moves_df for submissions
    submissions = moves_df[moves_df['categorization'] == 'Submission']['move_name'].unique()
    
    # Filter stats_df for submission moves
    submission_stats = stats_df[stats_df['move_name'].isin(submissions)]
    
    # Group by match and move_name for detailed counts
    grouped = submission_stats.groupby(['match', 'move_name']).agg({
        'offense_succeeded': 'sum',
        'defense_attempted': 'sum',
        'defense_succeeded': 'sum'
    }).reset_index()

    # Checking each match for the rules
    for match in grouped['match'].unique():
        match_data = grouped[grouped['match'] == match]

        # Collect successful offenses and unsuccessful defenses
        successful_submissions = match_data[match_data['offense_succeeded'] > 0]
        unsuccessful_defenses = match_data[match_data['defense_attempted'] - match_data['defense_succeeded'] > 0]
        
        if successful_submissions['offense_succeeded'].sum() > 1:
            success_details = ', '.join([f"{row['move_name']} ({row['offense_succeeded']} times)" for _, row in successful_submissions.iterrows()])
            context["errors"].append(f"Match {match} has multiple successful submission moves: {success_details}.")
            context["has_errors"] = True
        
        if (unsuccessful_defenses['defense_attempted'] - unsuccessful_defenses['defense_succeeded']).sum() > 1:
            defense_details = ', '.join([f"{row['move_name']} ({row['defense_attempted'] - row['defense_succeeded']} times)" for _, row in unsuccessful_defenses.iterrows()])
            context["errors"].append(f"Match {match} has multiple unsuccessful submission defenses: {defense_details}.")
            context["has_errors"] = True

        if successful_submissions['offense_succeeded'].sum() > 0 and (unsuccessful_defenses['defense_attempted'] - unsuccessful_defenses['defense_succeeded']).sum() > 0:
            context["errors"].append(f"Match {match} contains both successful submission offenses and unsuccessful defense attempts against submission moves.")
            context["has_errors"] = True

def validate_match_outcomes(stats_df, moves_df, results_df, context):
    
    # Identify submission moves from the moves dataframe
    submission_moves = moves_df[moves_df['categorization'] == 'Submission']['move_name'].unique()
    
    # Filter stats for submission moves and successful submissions
    successful_submissions = stats_df[(stats_df['move_name'].isin(submission_moves)) & (stats_df['offense_succeeded'] > 0)]
    
    # Validate successful submissions lead to a Win
    for _, row in successful_submissions.iterrows():
        match_result = results_df[results_df['match'] == row['match']]['Result'].iloc[0]
        if match_result != 'Win':
            context["errors"].append(f"Match {row['match']} has a successful submission but did not result in a Win.")
            context["has_errors"] = True
    
    # Filter stats for submission moves and unsuccessful defenses
    unsuccessful_defenses = stats_df[(stats_df['move_name'].isin(submission_moves)) & (stats_df['defense_attempted'] > 0) & (stats_df['defense_attempted'] > stats_df['defense_succeeded'])]
    
    # Validate unsuccessful defenses lead to a Loss
    for _, row in unsuccessful_defenses.iterrows():
        match_result = results_df[results_df['match'] == row['match']]['Result'].iloc[0]
        if match_result != 'Lost':
            context["errors"].append(f"Match {row['match']} has an unsuccessful defense submission but did not result in a Loss.")
            context["has_errors"] = True


def read_csv_from_s3(bucket_name, key):
    s3 = boto3.client('s3')
    response = s3.get_object(Bucket=bucket_name, Key=key)
    content = response['Body'].read().decode('utf-8')
    return pd.read_csv(StringIO(content))


def process_excel_file(ATHLETE_FILE):
    context = {"has_errors": False, "errors": []}

    # 🔧 Path to moves file (standard reference file)
    bucket_name = "jiu-jitsu-reporting"
    key = "lookups/moves_df.csv"
    moves_df = read_csv_from_s3(bucket_name, key)


    # 📥 Load Excel workbook
    xls = pd.ExcelFile(ATHLETE_FILE)
    
    # Validate and locate the Athlete sheet
    athlete_sheet = next((s for s in xls.sheet_names if "athlete" in s.lower()), None)
    if not athlete_sheet:
        return "No sheet named 'Athlete' found in the Excel file.", False

    # 📊 Step 1: Load data from Excel and CSV
    athlete_name, athlete_language, stats_df, results_df, moves_df = load_data(moves_df, xls, athlete_sheet)
    matches_data = {"Stats": stats_df, "Results": results_df}

    # ✅ Step 2: Run all validation checks
    check_missing_sheets(xls, context)
    validate_move_names(matches_data['Stats'], moves_df, context)
    validate_and_clean_numeric_fields(matches_data['Stats'], context)
    validate_defense_attempts_vs_succeeds(matches_data['Stats'], context)
    validate_offense_attempts_vs_succeeds(matches_data['Stats'], context)
    validate_submission_rules(matches_data['Stats'], moves_df, context)
    validate_match_outcomes(matches_data['Stats'], moves_df, matches_data['Results'], context)

    # ⚠️ Step 3: Handle validation errors, if any
    if context["has_errors"]:
        if athlete_name.endswith('s'):
            title = f"{athlete_name}' Jiu Jitsu Report Failure. Input Data has {len(context['errors'])} errors."
        else:
            title = f"{athlete_name}'s Jiu Jitsu Report Failure. Input Data has {len(context['errors'])} errors."

        body = title + "\n\n"
        body += "Hi. Requested Jiu Jitsu Report has failed to generate. Input Data has the following errors:\n\n"
        body += "\n".join(f"{index + 1}. {error}" for index, error in enumerate(context["errors"]))
        return body, False

    # 📊 Step 4: Group and prepare data for analysis
    grouped_df = prepare_grouped_data(stats_df, moves_df)

    # 🧠 Step 5: Generate AI summary (translated & gender-neutral)
    summary_text, json_data = generate_summary(stats_df, results_df, athlete_name, athlete_language)

    # 🧾 Step 6: Build final PDF data dictionary
    pdf_data = build_pdf_dict(
        name=athlete_name,
        language=athlete_language,
        stats_df=stats_df,
        results_df=results_df,
        moves_df=moves_df,
        grouped_df=grouped_df,
        summary_text=summary_text,
        json_data=json_data
    )

    # ✅ Return prepared PDF data and success status
    return pdf_data, True


# 🔁 Execute main function with default athlete file
# main(ATHLETE_FILE="file_example_XLSX_10.xlsx")
