import pandas as pd
import datetime
import matplotlib.pyplot as plt

def ratio_undersample(df:pd.DataFrame, frac:float):
    """
    Undersamples by a fraction rather than by class
    """
    n_samples = int(len(df)*frac)
    sub = df.sample(n=n_samples, random_state=1)
    return sub

def dataset_adaptor(df):
    text = df['text'].tolist()
    labels = df['label'].tolist()
    return [(x,y) for x,y in zip(text, labels)]

def format_time(elapsed):
    '''
    Takes a time in seconds and returns a string hh:mm:ss
    '''
    # Round to the nearest second.
    elapsed_rounded = int(round((elapsed)))
    # Format as hh:mm:ss
    return str(datetime.timedelta(seconds=elapsed_rounded))

def get_qc_examples(input_file):
  """Creates examples for the training and dev sets."""
  examples = []

  with open(input_file, 'r') as f:
      contents = f.read()
      file_as_list = contents.splitlines()
      for line in file_as_list[1:]:
          split = line.split(" ")
          question = ' '.join(split[1:])

          text_a = question
          inn_split = split[0].split(":")
          label = inn_split[0] + "_" + inn_split[1]
          examples.append((text_a, label))
      f.close()

  return examples

def get_stat_curve(dict_list, stat = ['Test f1_score_macro','Test f1_score_micro']):
    vals = {x:[] for x in stat}
    for d in dict_list:
        for s in stat:
            vals[s].append(d[s])
    for s in stat:
        plt.plot(vals[s], label=s)
        plt.legend(loc="upper left")